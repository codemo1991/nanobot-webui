"""Claude Code process manager."""

import asyncio
import json
import os
import platform
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.queue import MessageBus


class ClaudeCodeManager:
    """
    Manages Claude Code CLI processes.
    
    Responsibilities:
    - Start Claude Code with proper arguments
    - Track running tasks
    - Handle timeouts and cancellation
    - Generate Hook configuration
    - Relay user decision requests during execution (AskUserQuestion / permission prompts)
    """
    
    def __init__(
        self,
        workspace: Path,
        bus: MessageBus,
        result_dir: Path | None = None,
        default_timeout: int = 300,
        max_concurrent_tasks: int = 3,
    ):
        self.workspace = Path(workspace)
        self.bus = bus
        self.result_dir = result_dir or self.workspace / ".claude-results"
        self.default_timeout = default_timeout
        self.max_concurrent_tasks = max_concurrent_tasks
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_origins: dict[str, dict[str, str]] = {}
        self._watcher: "ResultWatcher | None" = None
        self._started = False
        # 用户决策中继：session_key -> (Future, questions)
        self._pending_decisions: dict[str, tuple[asyncio.Future, list]] = {}
        # 当前消息的来源渠道（由 ClaudeCodeTool.set_context 更新）
        self._channel: str = ""
        self._chat_id: str = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        """设置当前消息来源上下文，用于决策中继。"""
        self._channel = channel
        self._chat_id = chat_id

    def resolve_decision(self, session_key: str, answer: str) -> bool:
        """
        将用户的回复解析为等待中的 Claude Code 决策。
        
        Returns:
            True 表示成功路由；False 表示该 session 没有挂起的决策。
        """
        entry = self._pending_decisions.get(session_key)
        if entry:
            fut, _ = entry
            if not fut.done():
                fut.set_result(answer)
                return True
        return False

    def _inject_claude_settings_env(self, sdk_env: dict[str, str]) -> None:
        """
        读取 ~/.claude/settings.json 的 env 段并注入到 sdk_env。

        cc-switch 等代理工具通过写入此文件来配置 Claude Code 的 API 端点和认证信息
        （如 ANTHROPIC_BASE_URL、ANTHROPIC_AUTH_TOKEN）。SDK 以 stream-json 模式
        启动 claude.exe 时可能不会自动加载该文件的 env 段，因此需要手动注入。
        """
        try:
            settings_path = Path.home() / ".claude" / "settings.json"
            if not settings_path.exists():
                return
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
            claude_env: dict = settings.get("env", {})
            for key, value in claude_env.items():
                if isinstance(value, str) and key not in sdk_env:
                    sdk_env[key] = value
                elif not isinstance(value, str) and key not in sdk_env:
                    # 数值类型（如 API_TIMEOUT_MS: 3000000）转为字符串
                    sdk_env[key] = str(value)
            if claude_env:
                logger.debug(f"Claude Code: injected {len(claude_env)} env vars from ~/.claude/settings.json")
        except Exception as e:
            logger.debug(f"Could not read ~/.claude/settings.json: {e}")

    def _load_anthropic_key_from_db(self) -> str | None:
        """从 nanobot 配置数据库读取 Anthropic API Key。"""
        try:
            import sqlite3
            # nanobot 默认 DB 路径：~/.nanobot/chat.db
            db_path = Path.home() / ".nanobot" / "chat.db"
            if not db_path.exists():
                return None
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT api_key FROM config_providers WHERE id=? AND api_key IS NOT NULL AND api_key != ''",
                    ("anthropic",),
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not load Anthropic key from DB: {e}")
            return None
    
    def start_watcher(self) -> None:
        """Start the result file watcher."""
        if self._started:
            return
        
        from nanobot.claude_code.watcher import ResultWatcher
        
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self._watcher = ResultWatcher(
            result_dir=self.result_dir,
            bus=self.bus,
            on_result=self._handle_result,
        )
        self._watcher.start()
        self._started = True
        logger.info(f"Claude Code watcher started, watching {self.result_dir}")
    
    def stop_watcher(self) -> None:
        """Stop the result file watcher."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        self._started = False
    
    async def run_task(
        self,
        prompt: str,
        workdir: str | None = None,
        permission_mode: str = "auto",
        enable_subagents: bool = True,
        timeout: int | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """
        Run Claude Code task synchronously (await completion) via claude-agent-sdk.

        支持实时进度流、用户决策中继（AskUserQuestion / 权限审批）。
        当 enable_subagents=True 时，主 agent 可通过 Task 工具并行派发子任务到
        专用 subagent（代码探索、实现、命令执行），显著提升复杂任务执行速度。
        Returns dict with task_id, output, status.
        """
        from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        task_id = str(uuid.uuid4())[:8]

        workspace_resolved = self.workspace.resolve()
        if workdir:
            workdir_path = Path(workdir)
            if workdir_path.is_absolute():
                cwd = workdir_path.resolve()
            else:
                cwd = (workspace_resolved / workdir).resolve()
                try:
                    cwd.relative_to(workspace_resolved)
                except ValueError:
                    raise ValueError(
                        f"Relative path '{workdir}' resolves outside the workspace '{self.workspace}'. "
                        f"Use an absolute path if you need to access directories outside the workspace."
                    )
        else:
            cwd = workspace_resolved

        effective_timeout = timeout or self.default_timeout

        # 映射 permission_mode 到 SDK 格式
        sdk_perm_mode: str | None = None
        if permission_mode == "bypassPermissions":
            sdk_perm_mode = "bypassPermissions"
        elif permission_mode in ("plan", "acceptEdits", "default"):
            sdk_perm_mode = permission_mode

        async def can_use_tool(tool_name: str, input_data: dict, context: Any) -> Any:
            """拦截工具权限请求：决策类转发给用户，bypassPermissions 自动通过。"""
            if tool_name == "AskUserQuestion":
                return await self._relay_question_to_user(task_id, input_data, progress_callback)
            # 普通权限审批
            if sdk_perm_mode == "bypassPermissions":
                return PermissionResultAllow()
            return await self._relay_permission_to_user(task_id, tool_name, input_data, progress_callback)

        sdk_env: dict[str, str] = {
            # Windows 下强制 UTF-8，避免 Bash 工具运行 Python 脚本时因 GBK 编码崩溃
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        # 将 ~/.claude/settings.json 中的 env 段（cc-switch 等代理工具写入）
        # 显式注入到 SDK 进程环境，防止 SDK 的 stream-json 启动模式跳过文件读取
        self._inject_claude_settings_env(sdk_env)

        # 若仍没有认证信息，尝试从 nanobot 配置库读取 Anthropic API Key
        has_auth = any(k in sdk_env for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))
        if not has_auth and not os.environ.get("ANTHROPIC_API_KEY"):
            anthropic_key = self._load_anthropic_key_from_db()
            if anthropic_key:
                sdk_env["ANTHROPIC_API_KEY"] = anthropic_key
                logger.debug("Claude Code: loaded ANTHROPIC_API_KEY from nanobot config DB")

        # max_turns 防止 Claude Code 无限循环（如交互式脚本卡住 stdin）
        # 每轮约 10-30s，设为 timeout/10 且不超过 80 轮
        max_turns = min(80, max(10, effective_timeout // 10))

        # 追加系统提示：防止 Claude Code 用 <<EOF heredoc 测试交互式脚本
        # 在 Windows 上 heredoc stdin 会导致子进程永久阻塞
        _append_prompt = (
            "\n\n## 脚本测试规范（重要）\n"
            "当你创建含有 input() 等需要用户交互的脚本时，**禁止**使用 `<<EOF ... EOF` "
            "方式通过 stdin 管道传入测试数据，这在 Windows 环境下会导致进程永久卡住。\n"
            "请改用以下安全方式之一：\n"
            "1. 为脚本添加命令行参数支持（argparse），然后用 `python script.py --arg1 val1` 测试。\n"
            "2. 仅做语法检查：`python -m py_compile script.py`。\n"
            "3. 将交互逻辑拆成纯函数并编写单独的非交互测试脚本。\n"
            "4. 如脚本本身就是用户需要的成品，直接展示代码内容即可，无需运行验证。\n"
        )

        # 专用 subagent 定义：主 agent 可通过 Task 工具并行调度，提升复杂任务速度
        _agents: dict | None = None
        if enable_subagents:
            _agents = {
                "code-explorer": AgentDefinition(
                    description=(
                        "代码库探索与分析专家。用于快速搜索、阅读和理解代码结构、"
                        "依赖关系和现有实现。只读操作，适合并行探索多个文件或模块。"
                    ),
                    prompt=(
                        "你是一个代码库探索专家，专注于快速理解代码结构和架构。\n"
                        "你的职责：搜索相关文件、读取代码、分析依赖、整理信息。\n"
                        "规则：只做只读操作，不修改任何文件。返回清晰、结构化的分析结果。"
                    ),
                    tools=["Read", "Grep", "Glob", "LS"],
                    model="sonnet",
                ),
                "code-implementer": AgentDefinition(
                    description=(
                        "代码实现专家。用于编写新代码、修改已有代码、重构和创建文件。"
                        "适合独立完成一个功能模块或文件的实现。"
                    ),
                    prompt=(
                        "你是一个高质量代码实现专家，专注于编写简洁、可维护的代码。\n"
                        "你的职责：实现功能、修改代码、创建文件、处理边界情况。\n"
                        "规则：遵循现有代码风格，添加必要注释，确保代码完整可运行。"
                    ),
                    tools=["Read", "Write", "Edit", "MultiEdit", "Bash", "Grep", "Glob"],
                ),
                "command-runner": AgentDefinition(
                    description=(
                        "命令执行与测试验证专家。用于运行 shell 命令、执行测试、"
                        "安装依赖、验证代码是否正常工作。"
                    ),
                    prompt=(
                        "你是一个命令执行和测试验证专家，专注于确保代码正确运行。\n"
                        "你的职责：运行测试、执行命令、验证结果、报告错误信息。\n"
                        "规则：命令执行前先确认安全性，超时命令及时终止，清晰报告执行结果。"
                    ),
                    tools=["Bash", "Read"],
                ),
            }

        options = ClaudeAgentOptions(
            permission_mode=sdk_perm_mode,
            can_use_tool=can_use_tool,
            cwd=str(cwd),
            env=sdk_env,
            max_turns=max_turns,
            system_prompt={"type": "preset", "append": _append_prompt},
            **({"agents": _agents} if _agents else {}),
        )

        final_result = ""
        logger.info(f"Claude Code task [{task_id}] started (SDK): {prompt[:200]}")

        def _fire_progress(payload: dict) -> None:
            if progress_callback:
                try:
                    progress_callback(payload)
                except Exception:
                    pass

        _not_logged_in = False

        # can_use_tool 回调要求 prompt 以 AsyncIterable 形式传入（streaming mode）
        async def _prompt_stream():
            yield {"type": "user", "message": {"role": "user", "content": prompt}}

        async def _run_query() -> None:
            nonlocal final_result, _not_logged_in
            async for message in query(prompt=_prompt_stream(), options=options):
                msg_type = type(message).__name__
                if msg_type == "AssistantMessage":
                    for block in message.content:
                        block_type = type(block).__name__
                        if block_type == "TextBlock":
                            # 检测"未登录"提示，设置标志位
                            if "not logged in" in block.text.lower() or "please run /login" in block.text.lower():
                                _not_logged_in = True
                            final_result = block.text
                            _fire_progress({
                                "type": "claude_code_progress",
                                "task_id": task_id,
                                "subtype": "assistant_text",
                                "content": block.text[:500],
                                "timestamp": datetime.now().isoformat(),
                            })
                        elif block_type == "ToolUseBlock":
                            tool_input = block.input or {}
                            if block.name == "Task":
                                # subagent 调度事件：显示派发的子任务摘要
                                subagent_type = tool_input.get("subagent_type", "")
                                sub_prompt = tool_input.get("prompt", "")[:150]
                                desc = f"[{subagent_type}] {sub_prompt}" if subagent_type else sub_prompt
                                _fire_progress({
                                    "type": "claude_code_progress",
                                    "task_id": task_id,
                                    "subtype": "subagent_start",
                                    "tool_name": "Task",
                                    "subagent_type": subagent_type,
                                    "content": desc,
                                    "timestamp": datetime.now().isoformat(),
                                })
                            elif block.name == "Bash":
                                desc = tool_input.get("command", "")[:200]
                                _fire_progress({
                                    "type": "claude_code_progress",
                                    "task_id": task_id,
                                    "subtype": "tool_use",
                                    "tool_name": block.name,
                                    "content": desc,
                                    "timestamp": datetime.now().isoformat(),
                                })
                            elif block.name in ("Write", "Edit", "MultiEdit", "Read"):
                                desc = tool_input.get("file_path", "")
                                _fire_progress({
                                    "type": "claude_code_progress",
                                    "task_id": task_id,
                                    "subtype": "tool_use",
                                    "tool_name": block.name,
                                    "content": desc,
                                    "timestamp": datetime.now().isoformat(),
                                })
                            else:
                                desc = str(tool_input)[:200]
                                _fire_progress({
                                    "type": "claude_code_progress",
                                    "task_id": task_id,
                                    "subtype": "tool_use",
                                    "tool_name": block.name,
                                    "content": desc,
                                    "timestamp": datetime.now().isoformat(),
                                })
                elif msg_type == "ResultMessage":
                    if message.result:
                        if "not logged in" in message.result.lower() or "please run /login" in message.result.lower():
                            _not_logged_in = True
                        final_result = message.result

        try:
            await asyncio.wait_for(_run_query(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Claude Code [{task_id}] timed out after {effective_timeout}s")
            return {
                "task_id": task_id,
                "output": f"Task timed out after {effective_timeout} seconds",
                "status": "timeout",
            }
        except Exception as e:
            err_str = str(e)
            # 对"未登录/认证失败"给出更清晰的提示，避免 LLM 收到泛化错误后放弃重试
            if _not_logged_in or "not logged in" in err_str.lower() or "exit code 1" in err_str.lower():
                hint = (
                    "Claude Code 未认证：请在 nanobot 设置中填写 Anthropic API Key，"
                    "或在终端运行 `claude login` 完成登录后重启服务。"
                )
                logger.warning(f"Claude Code [{task_id}] auth error: {err_str[:200]}")
                return {"task_id": task_id, "output": hint, "status": "error"}
            logger.exception(f"Claude Code [{task_id}] SDK error: {e}")
            return {
                "task_id": task_id,
                "output": f"Error: {e}",
                "status": "error",
            }

        if _not_logged_in:
            hint = (
                "Claude Code 未认证：请在 nanobot 设置中填写 Anthropic API Key，"
                "或在终端运行 `claude login` 完成登录后重启服务。"
            )
            logger.warning(f"Claude Code [{task_id}] returned 'not logged in'")
            return {"task_id": task_id, "output": hint, "status": "error"}

        logger.info(f"Claude Code [{task_id}] completed successfully")
        return {
            "task_id": task_id,
            "output": final_result,
            "status": "done",
        }

    async def _relay_question_to_user(
        self,
        task_id: str,
        input_data: dict,
        progress_callback: Any = None,
    ) -> Any:
        """将 AskUserQuestion 的内容转发给用户，等待回复后返回 SDK 所需格式。"""
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
        from nanobot.bus.events import OutboundMessage

        questions: list[dict] = input_data.get("questions", [])

        # 格式化问题文本
        lines = [f"[Claude Code 任务 {task_id}] 需要你做决定：\n"]
        for i, q in enumerate(questions):
            header = q.get("header", f"问题 {i + 1}")
            question = q.get("question", "")
            options = q.get("options", [])
            lines.append(f"{header}：{question}")
            for j, opt in enumerate(options):
                label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                lines.append(f"  {j + 1}. {label}")
            lines.append("")
        lines.append("请回复选项编号或直接输入答案：")
        question_text = "\n".join(lines)

        session_key = f"{self._channel}:{self._chat_id}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_decisions[session_key] = (fut, questions)

        # 发送问题到用户
        if self.bus and self._channel and self._chat_id:
            out_msg = OutboundMessage(
                channel=self._channel,
                chat_id=self._chat_id,
                content=question_text,
                metadata={"claude_code_waiting_decision": True},
            )
            await self.bus.publish_outbound(out_msg)

        if progress_callback:
            try:
                progress_callback({
                    "type": "claude_code_progress",
                    "task_id": task_id,
                    "subtype": "waiting_user_decision",
                    "content": "等待用户决策...",
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception:
                pass

        try:
            answer_text = await asyncio.wait_for(asyncio.shield(fut), timeout=300)
        except asyncio.TimeoutError:
            self._pending_decisions.pop(session_key, None)
            return PermissionResultDeny(message="用户未在 5 分钟内回答，跳过此问题并继续。")
        finally:
            self._pending_decisions.pop(session_key, None)

        # 将用户输入映射回 SDK 期望的 answers 格式
        answers: dict[str, str] = {}
        for q in questions:
            qid = q.get("id", q.get("header", "q0"))
            options = q.get("options", [])
            try:
                idx = int(answer_text.strip()) - 1
                if 0 <= idx < len(options):
                    opt = options[idx]
                    answers[qid] = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                else:
                    answers[qid] = answer_text
            except ValueError:
                answers[qid] = answer_text

        return PermissionResultAllow(updated_input={**input_data, "answers": answers})

    async def _relay_permission_to_user(
        self,
        task_id: str,
        tool_name: str,
        input_data: dict,
        progress_callback: Any = None,
    ) -> Any:
        """将工具权限审批请求转发给用户，等待 y/n 回复。"""
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
        from nanobot.bus.events import OutboundMessage

        if tool_name == "Bash":
            desc = f"运行命令：`{input_data.get('command', '')[:200]}`"
        elif tool_name in ("Write", "Edit"):
            desc = f"修改文件：`{input_data.get('file_path', '')}`"
        elif tool_name == "Read":
            desc = f"读取文件：`{input_data.get('file_path', '')}`"
        else:
            desc = f"使用工具 {tool_name}"

        question_text = (
            f"[Claude Code 任务 {task_id}] 需要权限确认：\n"
            f"{desc}\n\n"
            "请回复 y（允许）或 n（拒绝）："
        )

        session_key = f"{self._channel}:{self._chat_id}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_decisions[session_key] = (fut, [])

        if self.bus and self._channel and self._chat_id:
            out_msg = OutboundMessage(
                channel=self._channel,
                chat_id=self._chat_id,
                content=question_text,
                metadata={"claude_code_waiting_decision": True},
            )
            await self.bus.publish_outbound(out_msg)

        try:
            answer_text = await asyncio.wait_for(asyncio.shield(fut), timeout=120)
        except asyncio.TimeoutError:
            self._pending_decisions.pop(session_key, None)
            return PermissionResultDeny(message="权限请求超时，已拒绝。")
        finally:
            self._pending_decisions.pop(session_key, None)

        if answer_text.strip().lower() in ("y", "yes", "是", "允许", "1"):
            return PermissionResultAllow()
        return PermissionResultDeny(message="用户拒绝了此操作。")

    async def start_task(
        self,
        prompt: str,
        workdir: str | None = None,
        permission_mode: str = "auto",
        agent_teams: bool = False,
        teammate_mode: str = "auto",
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        timeout: int | None = None,
        progress_callback: Any = None,
    ) -> str:
        """
        Start a Claude Code task and return task ID.
        
        Args:
            prompt: The task prompt for Claude Code.
            workdir: Working directory (defaults to workspace). Must be within workspace.
            permission_mode: Permission mode (auto/plan/acceptEdits/default/delegate/dontAsk/bypassPermissions).
                "auto" means no --permission-mode flag (use CLI default).
            agent_teams: Enable Agent Teams mode.
            teammate_mode: Teammate mode (auto/in-process/tmux).
            origin_channel: Origin channel for result notification.
            origin_chat_id: Origin chat ID for result notification.
            timeout: Task timeout in seconds.
            progress_callback: Optional callback for progress updates (receives dict with type, task_id, line).
        
        Returns:
            Task ID for tracking.
        
        Raises:
            ValueError: If workdir is outside the workspace.
        """
        if len(self._running_tasks) >= self.max_concurrent_tasks:
            raise RuntimeError(f"Maximum concurrent tasks ({self.max_concurrent_tasks}) reached")
        
        if not self._started:
            self.start_watcher()
        
        task_id = str(uuid.uuid4())[:8]
        
        # Resolve workdir with workspace restriction for relative paths
        workspace_resolved = self.workspace.resolve()
        if workdir:
            workdir_path = Path(workdir)
            if workdir_path.is_absolute():
                # Absolute path: allow any location
                cwd = workdir_path.resolve()
            else:
                # Relative path: must be within workspace
                cwd = (workspace_resolved / workdir).resolve()
                
                # Security check: ensure relative path doesn't escape workspace
                try:
                    cwd.relative_to(workspace_resolved)
                except ValueError:
                    raise ValueError(
                        f"Relative path '{workdir}' resolves outside the workspace '{self.workspace}'. "
                        f"Use an absolute path if you need to access directories outside the workspace."
                    )
        else:
            cwd = workspace_resolved
        
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }
        self._task_origins[task_id] = origin
        
        task_meta = {
            "task_id": task_id,
            "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
            "workdir": str(cwd),
            "origin": origin,
            "result_dir": str(self.result_dir),
            "timestamp": datetime.now().isoformat(),
        }
        task_meta_path = self.result_dir / f"{task_id}.meta.json"
        task_meta_path.write_text(json.dumps(task_meta, ensure_ascii=False), encoding="utf-8")
        
        bg_task = asyncio.create_task(
            self._run_claude_code(
                task_id=task_id,
                prompt=prompt,
                workdir=cwd,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                timeout=timeout or self.default_timeout,
                task_meta_path=task_meta_path,
                progress_callback=progress_callback,
            )
        )
        self._running_tasks[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))
        
        logger.info(f"Claude Code task [{task_id}] started: {task_meta['prompt']}")
        return task_id
    
    async def _run_claude_code(
        self,
        task_id: str,
        prompt: str,
        workdir: Path,
        permission_mode: str,
        agent_teams: bool,
        teammate_mode: str,
        timeout: int,
        task_meta_path: Path,
        progress_callback: Any = None,
    ) -> None:
        """Execute Claude Code process with optional progress streaming."""
        try:
            hook_script = self._get_hook_script_path()
            hook_script = hook_script.resolve()
            
            cmd = self._build_command(
                prompt=prompt,
                workdir=workdir,
                task_id=task_id,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                hook_script=hook_script,
                task_meta_path=task_meta_path,
            )
            
            logger.debug(f"Claude Code [{task_id}] command: {' '.join(cmd)}")
            
            env = os.environ.copy()
            env["NANOBOT_TASK_ID"] = task_id
            env["NANOBOT_TASK_META"] = str(task_meta_path)
            env["NANOBOT_RESULT_DIR"] = str(self.result_dir)
            
            # On Windows, npm-installed commands are .cmd files
            # We need to use shell=True or resolve the actual path
            if platform.system() == "Windows":
                process = await asyncio.create_subprocess_shell(
                    " ".join(self._quote_args(cmd)),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                    env=env,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                    env=env,
                )
            
            # Stream stdout for progress updates
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            
            async def read_stream(stream: asyncio.StreamReader | None, lines: list[str], stream_name: str) -> None:
                if not stream:
                    return
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    line_text = line.decode("utf-8", errors="replace").rstrip()
                    lines.append(line_text)
                    if stream_name == "stdout" and progress_callback:
                        try:
                            progress_callback({
                                "type": "claude_code_progress",
                                "task_id": task_id,
                                "line": line_text,
                                "timestamp": datetime.now().isoformat(),
                            })
                        except Exception as e:
                            logger.warning(f"Progress callback error: {e}")
            
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, stdout_lines, "stdout"),
                        read_stream(process.stderr, stderr_lines, "stderr"),
                        process.wait(),
                    ),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.warning(f"Claude Code [{task_id}] timed out after {timeout}s")
                await self._write_timeout_result(task_id, timeout)
                return
            
            if process.returncode != 0:
                stderr_text = "\n".join(stderr_lines)
                logger.error(f"Claude Code [{task_id}] failed with code {process.returncode}: {stderr_text}")
                await self._write_error_result(task_id, process.returncode, stderr_text)
            else:
                stdout_text = "\n".join(stdout_lines)
                logger.info(f"Claude Code [{task_id}] process completed successfully")
                await self._write_success_result(task_id, stdout_text)
                
        except Exception as e:
            logger.exception(f"Claude Code [{task_id}] error: {e}")
            await self._write_error_result(task_id, -1, str(e))
    
    def _build_command(
        self,
        prompt: str,
        workdir: Path,
        task_id: str,
        permission_mode: str,
        agent_teams: bool,
        teammate_mode: str,
        hook_script: Path,
        task_meta_path: Path,
    ) -> list[str]:
        """Build Claude Code CLI command."""
        cmd = ["claude"]
        
        if permission_mode == "bypassPermissions":
            cmd.append("--dangerously-skip-permissions")
        elif permission_mode in ("plan", "acceptEdits", "default", "delegate", "dontAsk"):
            cmd.extend(["--permission-mode", permission_mode])
        
        if agent_teams:
            cmd.append("--agent-teams")
            cmd.extend(["--teammate-mode", teammate_mode])
        
        hook_settings = {
            "hooks": {
                "Stop": [{
                    "hooks": [{
                        "type": "command",
                        "command": f'python "{hook_script}"',
                        "timeout": 10
                    }]
                }],
                "SessionEnd": [{
                    "hooks": [{
                        "type": "command",
                        "command": f'python "{hook_script}"',
                        "timeout": 10
                    }]
                }]
            }
        }
        hook_settings_path = self.result_dir / f"{task_id}.hook.json"
        hook_settings_path.write_text(json.dumps(hook_settings), encoding="utf-8")
        cmd.extend(["--settings", str(hook_settings_path)])
        
        cmd.append("-p")
        cmd.append(prompt)
        
        return cmd
    
    def _get_hook_script_path(self) -> Path:
        """Get path to the hook script, creating it if necessary."""
        hook_dir = Path(__file__).parent.parent / "hooks"
        hook_script = hook_dir / "nanobot-claude-hook.py"
        
        if not hook_script.exists():
            hook_script = self._create_hook_script(hook_script)
        
        return hook_script
    
    def _create_hook_script(self, path: Path) -> Path:
        """Create the hook script file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        
        script_content = '''#!/usr/bin/env python3
"""Claude Code Stop Hook for nanobot integration.

This script is called by Claude Code when a task completes.
It writes the result to a JSON file for nanobot to pick up.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main():
    task_meta_path = os.environ.get("NANOBOT_TASK_META", "")
    if not task_meta_path:
        sys.exit(0)
    
    task_meta_path = Path(task_meta_path)
    if not task_meta_path.exists():
        sys.exit(0)
    
    try:
        with open(task_meta_path, encoding="utf-8") as f:
            task_meta = json.load(f)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)
    
    task_id = task_meta.get("task_id", "unknown")
    result_dir = Path(task_meta.get("result_dir", "."))
    origin = task_meta.get("origin", {})
    
    output = ""
    for env_var in ["CLAUDE_OUTPUT", "CLAUDE_CODE_OUTPUT"]:
        if env_var in os.environ:
            output = os.environ[env_var]
            break
    
    lock_file = result_dir / f".{task_id}.lock"
    if lock_file.exists():
        sys.exit(0)
    
    try:
        lock_file.touch()
    except IOError:
        sys.exit(0)
    
    try:
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": output,
            "status": "done",
            "origin": origin,
        }
        
        result_path = result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    finally:
        try:
            lock_file.unlink()
        except IOError:
            pass


if __name__ == "__main__":
    main()
'''
        path.write_text(script_content, encoding="utf-8")
        logger.info(f"Created hook script at {path}")
        return path
    
    async def _write_timeout_result(self, task_id: str, timeout: int) -> None:
        """Write a timeout result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": f"Task timed out after {timeout} seconds",
            "status": "timeout",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    
    async def _write_error_result(self, task_id: str, code: int, error: str) -> None:
        """Write an error result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": f"Error (code {code}): {error}",
            "status": "error",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    async def _write_success_result(self, task_id: str, output: str) -> None:
        """Write a success result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": output,
            "status": "done",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    async def _write_cancelled_result(self, task_id: str) -> None:
        """Write a cancelled result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": "Task was cancelled by user",
            "status": "cancelled",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    def _handle_result(self, result: dict[str, Any]) -> None:
        """Handle a result from the watcher."""
        task_id = result.get("task_id", "")
        self._task_origins.pop(task_id, None)
    
    def get_running_count(self) -> int:
        """Return the number of currently running tasks."""
        return len(self._running_tasks)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """
        Get details of a single task by ID.

        Args:
            task_id: The task ID to retrieve.

        Returns:
            Task info dict with task_id, prompt, status, start_time, end_time, result,
            or None if task not found.
        """
        # Check if task is still running
        if task_id in self._running_tasks:
            task = self._running_tasks[task_id]
            # Load metadata from meta file
            meta_path = self.result_dir / f"{task_id}.meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    return {
                        "task_id": task_id,
                        "prompt": meta.get("prompt", ""),
                        "status": "running" if not task.done() else "done",
                        "start_time": meta.get("timestamp", ""),
                        "end_time": None,
                        "result": None,
                        "workdir": meta.get("workdir", ""),
                        "origin": meta.get("origin", {}),
                    }
                except (json.JSONDecodeError, IOError):
                    pass

            return {
                "task_id": task_id,
                "prompt": "",
                "status": "running" if not task.done() else "done",
                "start_time": None,
                "end_time": None,
                "result": None,
                "workdir": None,
                "origin": {},
            }

        # Task completed - read from result file
        result_path = self.result_dir / f"{task_id}.json"
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                # Also try to get metadata for prompt
                meta_path = self.result_dir / f"{task_id}.meta.json"
                prompt = ""
                workdir = None
                origin = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        prompt = meta.get("prompt", "")
                        workdir = meta.get("workdir", "")
                        origin = meta.get("origin", {})
                    except (json.JSONDecodeError, IOError):
                        pass

                return {
                    "task_id": task_id,
                    "prompt": prompt,
                    "status": result.get("status", "unknown"),
                    "start_time": result.get("timestamp", ""),
                    "end_time": result.get("timestamp", ""),
                    "result": result.get("output", ""),
                    "workdir": workdir,
                    "origin": origin,
                }
            except (json.JSONDecodeError, IOError):
                pass

        return None

    def get_all_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str = "all",
    ) -> dict[str, Any]:
        """
        Get all Claude Code tasks (both running and completed) with pagination.

        Args:
            page: Page number (1-indexed).
            page_size: Number of items per page.
            status: Filter by status ("running", "done", or "all").

        Returns:
            Dict with items array and total count.
        """
        tasks: list[dict[str, Any]] = []

        # Get running tasks
        for task_id in self._running_tasks:
            task_info = self.get_task(task_id)
            if task_info:
                tasks.append(task_info)

        # Get completed tasks from result files
        if self.result_dir.exists():
            for file_path in self.result_dir.glob("*.json"):
                # Skip meta and hook files
                if file_path.name.endswith(".meta.json") or file_path.name.endswith(".hook.json"):
                    continue
                if file_path.name.startswith("."):
                    continue

                task_id = file_path.stem
                # Skip if already in running tasks
                if task_id in self._running_tasks:
                    continue

                task_info = self.get_task(task_id)
                if task_info:
                    tasks.append(task_info)

        # Filter by status
        if status != "all":
            tasks = [t for t in tasks if t.get("status") == status]

        # Sort by start_time descending (most recent first)
        tasks.sort(key=lambda x: x.get("start_time", ""), reverse=True)

        # Calculate pagination
        total = len(tasks)
        safe_page = max(1, page)
        safe_page_size = max(1, min(page_size, 100))
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        paginated_items = tasks[start:end]

        return {
            "items": paginated_items,
            "page": safe_page,
            "pageSize": safe_page_size,
            "total": total,
        }

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._running_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            # Write cancelled result file synchronously
            try:
                origin = self._task_origins.pop(task_id, {})
                result = {
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                    "output": "Task was cancelled by user",
                    "status": "cancelled",
                    "origin": origin,
                }
                result_path = self.result_dir / f"{task_id}.json"
                result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to write cancelled result file: {e}")
            logger.info(f"Claude Code task [{task_id}] cancelled")
            return True
        return False

    def cancel_by_session(self, channel: str, chat_id: str) -> int:
        """Cancel all Claude Code tasks for the given session. Returns count of cancelled tasks."""
        origin_key = f"{channel}:{chat_id}"
        cancelled = 0
        for task_id, origin in list(self._task_origins.items()):
            task_origin_key = f"{origin.get('channel', '')}:{origin.get('chat_id', '')}"
            if task_origin_key == origin_key:
                if self.cancel_task(task_id):
                    cancelled += 1
        if cancelled > 0:
            logger.info(f"Claude Code: cancelled {cancelled} tasks for session {origin_key}")
        return cancelled

    def check_claude_available(self) -> bool:
        """Check if Claude Code CLI is available."""
        available = shutil.which("claude") is not None
        logger.info(f"[ClaudeCodeManager] Claude Code CLI available: {available}")
        return available
    
    def _quote_args(self, args: list[str]) -> list[str]:
        """Quote command arguments for shell execution on Windows."""
        quoted = []
        for arg in args:
            if " " in arg or '"' in arg or "'" in arg:
                quoted.append(f'"{arg.replace(chr(34), chr(92) + chr(34))}"')
            else:
                quoted.append(arg)
        return quoted
