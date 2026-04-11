"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import estimate_tokens, truncate_to_token_limit


TOKEN_BUDGET_DEFAULTS = {
    "identity": 500,
    "bootstrap": 1500,
    "memory": 2000,
    "skills": 500,
    "total": 5000,
}

SKILLS_GUIDANCE = (
    "## Skill Creation Guidance\n"
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manage so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manage(action='patch') — don't wait to be asked.\n"
    "Skills are stored at {workspace}/skills/ and persist across sessions."
)

# 主 Agent 默认身份内容（不含 runtime_suffix），用于启动时初始化 DB 及内置回退
# Media Handling 部分由 ContextBuilder 动态构建模板描述
DEFAULT_IDENTITY_CONTENT = """# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Behavior Guidelines

- Be helpful, accurate, and concise
- Use tools when needed, explain what you're doing
- When user says "记住/remember", call the remember tool to persist the information (global long-term memory)
- After self-improving-agent / retrospective: you MUST call **persist_self_improvement** for each lesson to store in SQLite (scope=self_improve); editing skill JSON alone does not write to DB
- For normal conversation, respond with text directly. Only use the 'message' tool for cross-channel messaging.

## When to Use spawn (Subagent)

- **Use spawn** for tasks that need dedicated capability or longer processing: image/voice analysis (vision, voice template), deep research (researcher), code implementation (coder), data analysis (analyst)
- **Do NOT spawn** for simple one-off operations: reading a file, running a command, web search — do these yourself
- **Avoid duplicate spawn**: only spawn once per logical task; do not create similar subagents for the same request
- **Task description**: When spawning with images, keep the task description brief and aligned with user's original intent. Do NOT re-describe the image; simply pass the user's request as-is (e.g., if user says "describe this image in mermaid", use exactly that as the task)
- **Non-blocking**: After spawning, immediately return the result to the user. Do NOT wait or poll - the subagent will notify you when complete via a system message.
- **Handle subagent results**: When you receive a system message with subagent results, synthesize them into your reply to the user.
- **Self-improvement (optional)**: After a long or skill-heavy task, if the user wants repeatable lessons captured, you may briefly suggest the **self-improving-agent** skill (paths in Skills) — do not block the main answer on this.

## Subagent Templates

Use spawn tool to delegate tasks to subagents. Available templates are listed in the tool description."""


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.

    Supports agent-specific memory isolation when agent_id is provided.
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]

    def __init__(self, workspace: Path, agent_id: str | None = None, token_budget: dict[str, int] | None = None, agent_template_manager: Any = None):
        self.workspace = workspace
        self.agent_id = agent_id
        self.memory = MemoryStore(workspace, agent_id=agent_id)
        self.skills = SkillsLoader(workspace)
        self.token_budget = {**TOKEN_BUDGET_DEFAULTS, **(token_budget or {})}
        self._agent_template_manager = agent_template_manager
        self._skill_snapshot: str | None = None  # 冻结的 skills 索引快照

    def update_token_budget(self, **kwargs: int) -> None:
        """Update token budget settings at runtime."""
        self.token_budget.update(kwargs)
    
    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        max_tokens: int | None = None,
        user_message: str | None = None,
        dynamic_skills: bool = True,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
            max_tokens: Maximum tokens for the system prompt. Uses token_budget["total"] if not specified.
            user_message: Optional user message for dynamic skill matching.
            dynamic_skills: If True, use dynamic skill loading based on user message.
        
        Returns:
            Complete system prompt.
        """
        budget = max_tokens or self.token_budget["total"]
        parts = []
        current_tokens = 0
        
        identity = self._get_identity()
        identity_tokens = estimate_tokens(identity)
        if identity_tokens > self.token_budget["identity"]:
            identity = truncate_to_token_limit(identity, self.token_budget["identity"])
            identity_tokens = estimate_tokens(identity)
        parts.append(identity)
        current_tokens += identity_tokens
        
        remaining = max(0, budget - current_tokens)
        bootstrap_budget = min(
            self.token_budget["bootstrap"],
            remaining - self.token_budget["memory"] - self.token_budget["skills"]
        )
        if bootstrap_budget > 200:
            bootstrap = self._load_bootstrap_files()
            if bootstrap:
                bootstrap_tokens = estimate_tokens(bootstrap)
                if bootstrap_tokens > bootstrap_budget:
                    bootstrap = truncate_to_token_limit(bootstrap, bootstrap_budget)
                parts.append(bootstrap)
                current_tokens += estimate_tokens(bootstrap)
        
        remaining = max(0, budget - current_tokens)
        memory_budget = min(
            self.token_budget["memory"],
            remaining - self.token_budget["skills"]
        )
        if memory_budget > 200:
            memory = self.memory.get_memory_context(max_tokens=memory_budget)
            if memory:
                parts.append(f"# Memory\n\n{memory}")
                current_tokens += estimate_tokens(memory)
        
        always_skills_budget = budget - current_tokens - self.token_budget["skills"]
        always_skills = self.skills.get_always_skills()
        if always_skills and always_skills_budget > 0:
            always_index = self.skills.build_skill_paths_index(always_skills)
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_index or always_content:
                blocks: list[str] = []
                if always_index:
                    blocks.append("### Skill paths\n\n" + always_index)
                if always_content:
                    blocks.append(always_content)
                merged = "\n\n---\n\n".join(blocks)
                always_tokens = estimate_tokens(merged)
                if always_tokens > always_skills_budget:
                    merged = truncate_to_token_limit(merged, always_skills_budget)
                    always_tokens = estimate_tokens(merged)
                parts.append(f"# Active Skills\n\n{merged}")
                current_tokens += always_tokens
        
        skills_budget = min(
            self.token_budget["skills"],
            budget - current_tokens
        )
        if skills_budget > 50:
            if dynamic_skills and user_message:
                skills_summary = self.skills.build_dynamic_summary(user_message)
            else:
                skills_summary = self.skills.build_skills_summary(level=0)
            
            if skills_summary:
                skills_tokens = estimate_tokens(skills_summary)
                if skills_tokens > skills_budget:
                    skills_summary = truncate_to_token_limit(skills_summary, skills_budget)
                parts.append(f"""# Skills

Skills extend your capabilities. To use a skill, read its SKILL.md file with read_file tool.
Each line lists `SKILL.md` and **dir** (the skill folder; use for `memory/`, `references/`, etc.).
(✓ = available, ✗ = missing dependencies)

{skills_summary}""")

        # Skill 创建引导（始终注入，提醒 agent 使用 skill_manage 创建/维护 skills）
        guidance = SKILLS_GUIDANCE.replace("{workspace}", str(self.workspace))
        parts.append(guidance)

        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """获取主 Agent 身份区块。优先级：DB 配置 > IDENTITY.md > 内置默认。"""
        from datetime import datetime

        from nanobot.storage import memory_repository
        from nanobot.storage.main_agent_prompt_repository import MainAgentPromptRepository

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        today_ymd = datetime.now().strftime("%Y-%m-%d")
        workspace_path = str(self.workspace.expanduser().resolve())

        # 动态构建模板描述
        template_info = ""
        if self._agent_template_manager:
            try:
                templates = self._agent_template_manager.list_templates()
                if templates:
                    template_lines = []
                    for t in templates:
                        if t.enabled:
                            desc = t.description or "无描述"
                            template_lines.append(f"- **{t.name}**: {desc}")
                    if template_lines:
                        template_info = "\n\n## Available Subagent Templates\n\n" + "\n".join(template_lines)
            except Exception:
                pass

        runtime_suffix = f"""

## 今日日期（重要）
**Today's Date**: {today_ymd}
当前时间: {now}

进行记录、日记、笔记、记忆等操作时，必须使用上述今日日期 {today_ymd}，不要基于对话历史或上下文推断日期。

## Workspace
{workspace_path}
- 长期记忆与日程笔记：存储在 {workspace_path}/.nanobot/chat.db（SQLite），使用 remember 工具写入（scope=global）
- 自我改进可检索结论：同一数据库 memory_entries，scope=self_improve，使用 persist_self_improvement 工具写入（与 global 记忆总结互不覆盖）
- User skills: {workspace_path}/skills/
- Skill paths: see Skills section below (`SKILL.md` path and **dir** = skill root next to it)
{template_info}
"""
        # 1. 优先从 SQLite 读取用户可视化配置
        db_path = memory_repository.MemoryRepository.get_workspace_db_path(self.workspace)
        if not db_path.exists():
            db_path = memory_repository.MemoryRepository.get_default_db_path()
        repo = MainAgentPromptRepository(db_path)
        row = repo.get(workspace_path)
        if row and (row.get("identity_content") or "").strip():
            return (row["identity_content"].rstrip() + runtime_suffix)
        # 2. 其次从 workspace/IDENTITY.md
        identity_file = self.workspace / "IDENTITY.md"
        if identity_file.exists():
            content = identity_file.read_text(encoding="utf-8").strip()
            if content:
                return content.rstrip() + runtime_suffix
        # 3. 内置默认
        return DEFAULT_IDENTITY_CONTENT.rstrip() + runtime_suffix
    
    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")
        
        return "\n\n".join(parts) if parts else ""

    def invalidate_skill_snapshot(self) -> None:
        """清除 skills 索引快照，使下一轮对话重新扫描 skills/ 目录。"""
        self._skill_snapshot = None

    def _build_skills_index(self) -> str:
        """构建 skills 索引文本。返回冻结快照，支持 session 级缓存。"""
        if self._skill_snapshot is not None:
            return self._skill_snapshot

        index_lines: list[str] = []
        skills_dir = self.workspace / "skills"
        if skills_dir.exists():
            for skill_path in sorted(skills_dir.iterdir()):
                if skill_path.is_dir():
                    skill_md = skill_path / "SKILL.md"
                    if skill_md.exists():
                        index_lines.append(f"- **{skill_path.name}**")

        snapshot = "\n".join(index_lines)
        self._skill_snapshot = snapshot
        return snapshot

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        mirror_attack_level: str | None = None,
        dynamic_skills: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            mirror_attack_level: For mirror bian sessions, the attack level (light/medium/heavy).
            dynamic_skills: If True, use dynamic skill loading based on user message.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        system_prompt = self.build_system_prompt(
            skill_names=skill_names,
            user_message=current_message,
            dynamic_skills=dynamic_skills,
        )
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"

        # 添加指导：spawn 子 agent 后不要给出额外的不必要说明
        system_prompt += "\n\n## 重要提示\n当 spawn 后台子 agent 后，直接返回任务已启动的简要说明即可，不要解释为什么要 spawn 子 agent（如不要提及'MiniMax模型生成的图像'、'无法直接访问OSS'等技术细节）。"
        CHANNEL_MIRROR = "mirror"
        ATTACK_DESCRIPTIONS = {
            "light": "友善追问，点到为止，不施加压力",
            "medium": "举例反例，适度施压，温和挑战用户观点",
            "heavy": "直指双标与矛盾，犀利追问，强压力测试",
        }
        if mirror_attack_level and channel == CHANNEL_MIRROR:
            desc = ATTACK_DESCRIPTIONS.get(
                mirror_attack_level.lower() if isinstance(mirror_attack_level, str) else "",
                ATTACK_DESCRIPTIONS["medium"],
            )
            system_prompt += (
                f"\n\n## 镜室-辩论模式\n本轮攻击强度: {mirror_attack_level}\n"
                f"请严格按此风格追问: {desc}"
            )
        messages.append({"role": "system", "content": system_prompt})

        messages.extend(history)

        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images and audio files."""
        if not media:
            return text

        images = []
        audio_files = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            is_image = (mime and mime.startswith("image/")) or ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            is_audio = (mime and mime.startswith("audio/")) or ext in (".mp3", ".wav", ".ogg", ".m4a", ".opus", ".webm", ".aac")
            # 处理图片
            if is_image:
                mime_type = mime or "image/jpeg"
                b64 = base64.b64encode(p.read_bytes()).decode()
                images.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}})
            # 处理音频（飞书 .ogg 等可能无 mime，用扩展名兜底）
            elif is_audio:
                audio_files.append({"type": "audio_file", "audio_url": {"url": p.as_posix()}})

        # 如果有图片或音频，构造多模态消息
        content_parts = []
        if images:
            content_parts.extend(images)
        if audio_files:
            # 音频文件路径作为文本提示，告诉模型有音频待处理
            audio_paths = "\n".join([f"[Attached Audio: {a['audio_url']['url']}]" for a in audio_files])
            content_parts.append({"type": "text", "text": f"{audio_paths}\n\n{text}" if text else audio_paths})
        elif content_parts:
            # 仅有图片时，必须附加用户文本，否则 LLM 可能无法正确理解请求（部分模型/实现会忽略纯图片消息）
            content_parts.append({"type": "text", "text": text or "请描述图片内容"})

        if not content_parts:
            return text
        # 只有图片时返回多模态内容，有音频时返回混合内容
        return content_parts
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        # 确保 tool_call_id 为字符串（部分 API 如 MiniMax 对类型敏感）
        messages.append({
            "role": "tool",
            "tool_call_id": str(tool_call_id) if tool_call_id is not None else "",
            "name": tool_name,
            "content": result if result is not None else ""
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        messages.append(msg)
        return messages


def repair_openai_tool_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """
    修复 OpenAI 格式的 tool 消息链，满足「每条 assistant.tool_calls 均有对应 tool 消息」且顺序与 id 一致。

    - 为缺失的 tool_call_id 插入占位 tool 消息（MiniMax 2013）
    - 按 tool_calls 顺序重排紧随其后的 tool 消息，丢弃多余或重复的 tool 行

    Returns:
        (新消息列表, 是否发生了修改)
    """
    out: list[dict[str, Any]] = []
    changed = False
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            out.append(m)
            i += 1
            continue

        tcs = m["tool_calls"]
        if not tcs:
            out.append(m)
            i += 1
            continue

        out.append(m)
        j = i + 1
        tool_block: list[dict[str, Any]] = []
        while j < len(messages) and messages[j].get("role") == "tool":
            tool_block.append(messages[j])
            j += 1

        by_id: dict[str, dict[str, Any]] = {}
        for t in tool_block:
            tid = str(t.get("tool_call_id", "") or "")
            if tid and tid not in by_id:
                by_id[tid] = t

        merged: list[dict[str, Any]] = []
        for tc in tcs:
            tid = str(tc.get("id", "") or "")
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = (fn.get("name") if isinstance(fn, dict) else None) or "unknown"
            if tid in by_id:
                orig = by_id[tid]
                if str(orig.get("tool_call_id", "") or "") != tid:
                    merged.append(
                        {
                            **orig,
                            "tool_call_id": tid,
                            "name": orig.get("name") or name,
                        }
                    )
                    changed = True
                else:
                    merged.append(orig)
            else:
                merged.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "name": name,
                        "content": (
                            "[系统补全] 该工具调用缺少对应的 tool 返回消息，已插入占位以满足 API 要求。"
                            "请根据上下文继续回答，必要时可重新调用工具。"
                        ),
                    }
                )
                changed = True

        orig_ids = [str(t.get("tool_call_id", "") or "") for t in tool_block]
        merged_ids = [str(t.get("tool_call_id", "") or "") for t in merged]
        if orig_ids != merged_ids or len(tool_block) != len(merged):
            changed = True

        out.extend(merged)
        i = j
    return out, changed
