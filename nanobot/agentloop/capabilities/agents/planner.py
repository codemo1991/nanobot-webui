"""Planner Agent：规划任务并 spawn 子任务。支持 LLM 动态规划。"""

import json
import re

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult, TaskSpec


CAPABILITY_DESCRIPTIONS = """
可用工具说明：
- web_search_tool：通过 Brave API 搜索网页，获取外部信息
- read_file_tool：读取本地文件内容（仅当用户明确提到文件路径时使用）
- exec_tool：执行本地命令（仅当用户明确要求执行命令时使用，禁止 rm/del/format/shutdown 等危险命令）
- retriever_group_reducer：合并多个搜索结果为证据包
- drafter_agent：根据 plan 和证据起草方案
- critic_agent：评审草案，指出风险和遗漏
- final_reducer：聚合所有产出，生成最终结果
"""

LLM_PLAN_PROMPT = """你是一个任务规划专家。请分析用户请求，决定需要调用哪些工具来完成任务。

{capabilities}

用户请求：{goal}

此前已尝试的步骤：{attempted}
已有初始产物：{initial_keys}

请返回严格 JSON 格式（不要包含 markdown 代码块标记）：
{{
  "analysis": "对任务的简要分析",
  "needs_search": true/false,
  "needs_read_file": true/false,
  "file_path": "文件路径（如果需要，否则留空）",
  "needs_exec": true/false,
  "exec_command": "命令（如果需要，否则留空）",
  "reasoning": "为什么这样规划"
}}
"""


def _is_likely_url(s: str) -> bool:
    """排除 URL，避免将网页链接误识别为文件路径。"""
    lower = s.strip().lower()
    return lower.startswith(("http://", "https://", "www."))


def _should_add_read_file(goal: str, initial_keys: list[str], attempted: list[dict]) -> tuple[bool, str]:
    """若 initial_artifacts 已有 doc_content_v1 或 attempted_steps 已有 read_file，则不 spawn。"""
    if "doc_content_v1" in initial_keys:
        return False, ""
    for s in attempted:
        if (s.get("name") or "").lower() in ("read_file", "read file"):
            return False, ""
    goal_lower = goal.lower()
    if not any(kw in goal_lower for kw in ("读", "read", "文件", "file", "查看", "打开")):
        return False, ""
    path_match = re.search(r"(?:读|读取|查看|打开)[\s:：]*([^\s，,。]+\.\w{2,5})", goal)
    if path_match:
        path = path_match.group(1).strip()
        if not _is_likely_url(path):
            return True, path
    path_match = re.search(r"([a-zA-Z]:\\[^\s]+|/[^\s]+\.\w{2,5})", goal)
    if path_match:
        path = path_match.group(1).strip()
        if not _is_likely_url(path):
            return True, path
    return False, ""


def _should_add_web_search(goal: str, initial_keys: list[str]) -> bool:
    """若已有 search_result_v1 可跳过；否则根据 goal 判断是否需要搜索。"""
    if "search_result_v1" in initial_keys:
        return False
    return len(goal.strip()) > 5


def _should_add_exec(goal: str, initial_keys: list[str]) -> tuple[bool, str]:
    """保守：仅当 goal 明确包含简单命令时 spawn exec。"""
    if "exec_output_v1" in initial_keys:
        return False, ""
    goal_lower = goal.lower()
    if not any(kw in goal_lower for kw in ("执行", "run", "运行", "command", "命令")):
        return False, ""
    cmd_match = re.search(r"(?:执行|run|运行)[\s:：]*([^\n。]+)", goal, re.I)
    if cmd_match:
        cmd = cmd_match.group(1).strip()
        if len(cmd) < 100 and not any(x in cmd for x in ["rm ", "del ", "format", "shutdown"]):
            return True, cmd
    return False, ""


def _safe_json_parse(text: str) -> dict:
    """从 LLM 返回文本中提取 JSON 对象。"""
    # 先尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 尝试从 markdown 代码块中提取
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 尝试提取第一个 { ... }
    match = re.search(r"(\{[\s\S]*\})", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    return {}


class PlannerAgent(Capability):
    """规划 agent，根据 goal/attempted_steps/initial_artifacts 动态 spawn 子任务。
    支持 LLM 增强规划，LLM 不可用时回退到规则匹配。
    """

    name = "planner_agent"
    kind = "agent"

    async def _llm_plan(
        self, goal: str, attempted: list[dict], initial_keys: list[str], provider, model: str
    ) -> dict:
        """调用 LLM 获取动态规划建议（带重试）。"""
        prompt = LLM_PLAN_PROMPT.format(
            capabilities=CAPABILITY_DESCRIPTIONS,
            goal=goal,
            attempted=json.dumps(attempted, ensure_ascii=False) if attempted else "无",
            initial_keys=", ".join(initial_keys) if initial_keys else "无",
        )
        messages = [
            {"role": "system", "content": "你是一个任务规划专家，只返回 JSON。"},
            {"role": "user", "content": prompt},
        ]
        for attempt in range(3):
            try:
                response = await provider.chat(
                    messages=messages,
                    model=model,
                    max_tokens=1024,
                    temperature=0.3,
                )
                text = response.content or ""
                if text.strip():
                    return _safe_json_parse(text)
            except Exception:
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(0.5 * (attempt + 1))
        return {}

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        goal = request.get("user_goal", request.get("goal", ""))
        attempted = request.get("attempted_steps", [])
        initial_keys = request.get("initial_artifacts_keys", [])

        spawn_specs: list[TaskSpec] = []
        llm_analysis = ""

        provider = context.get("provider")
        model = context.get("model")

        # 尝试 LLM 动态规划
        if provider and model:
            try:
                plan = await self._llm_plan(goal, attempted, initial_keys, provider, model)
                llm_analysis = plan.get("analysis", "")

                # LLM 建议搜索
                if plan.get("needs_search") and _should_add_web_search(goal, initial_keys):
                    search_cap = "web_search_tool"
                    spawn_specs.append(TaskSpec(
                        task_kind="TOOL",
                        capability_name=search_cap,
                        intent="retrieve_evidence",
                        priority=20,
                        output_schema="search_result_v1",
                        request_payload={"query": goal[:200], "count": 5},
                    ))

                # LLM 建议读取文件（仍需经过安全校验）
                llm_file_path = plan.get("file_path", "")
                if plan.get("needs_read_file") and llm_file_path:
                    # 额外校验：排除 URL 和危险路径
                    if not _is_likely_url(llm_file_path) and ".." not in llm_file_path:
                        spawn_specs.append(TaskSpec(
                            task_kind="TOOL",
                            capability_name="read_file_tool",
                            intent="read_file",
                            priority=18,
                            output_schema="doc_content_v1",
                            request_payload={"path": llm_file_path},
                        ))

                # LLM 建议执行命令（仍需经过安全校验）
                llm_cmd = plan.get("exec_command", "")
                if plan.get("needs_exec") and llm_cmd:
                    if len(llm_cmd) < 100 and not any(x in llm_cmd for x in ["rm ", "del ", "format", "shutdown"]):
                        spawn_specs.append(TaskSpec(
                            task_kind="TOOL",
                            capability_name="exec_tool",
                            intent="exec_command",
                            priority=18,
                            output_schema="exec_output_v1",
                            request_payload={"command": llm_cmd},
                        ))
            except Exception as exc:
                llm_analysis = f"LLM 规划失败，回退到规则匹配。错误: {exc}"

        # 如果 LLM 没有 spawn 任何任务，或 LLM 不可用，回退到规则匹配
        if not spawn_specs:
            search_cap = "web_search_tool"
            if _should_add_web_search(goal, initial_keys):
                spawn_specs.append(TaskSpec(
                    task_kind="TOOL",
                    capability_name=search_cap,
                    intent="retrieve_evidence",
                    priority=20,
                    output_schema="search_result_v1",
                    request_payload={"query": goal[:200], "count": 5},
                ))
            else:
                spawn_specs.extend([
                    TaskSpec(
                        task_kind="TOOL",
                        capability_name="search_tool",
                        intent="retrieve_evidence",
                        priority=20,
                        output_schema="search_result_v1",
                        request_payload={"query": goal, "slot": 1},
                    ),
                    TaskSpec(
                        task_kind="TOOL",
                        capability_name="search_tool",
                        intent="retrieve_evidence",
                        priority=20,
                        output_schema="search_result_v1",
                        request_payload={"query": goal, "slot": 2},
                    ),
                ])

            add_read, path = _should_add_read_file(goal, initial_keys, attempted)
            if add_read and path:
                spawn_specs.append(TaskSpec(
                    task_kind="TOOL",
                    capability_name="read_file_tool",
                    intent="read_file",
                    priority=18,
                    output_schema="doc_content_v1",
                    request_payload={"path": path},
                ))

            add_exec, cmd = _should_add_exec(goal, initial_keys)
            if add_exec and cmd:
                spawn_specs.append(TaskSpec(
                    task_kind="TOOL",
                    capability_name="exec_tool",
                    intent="exec_command",
                    priority=18,
                    output_schema="exec_output_v1",
                    request_payload={"command": cmd},
                ))

        # 固定下游任务
        spawn_specs.extend([
            TaskSpec(
                task_kind="REDUCER",
                capability_name="retriever_group_reducer",
                intent="merge_evidence",
                priority=22,
                input_schema="search_result_v1",
                output_schema="evidence_bundle_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="AGENT",
                capability_name="drafter_agent",
                intent="draft_solution",
                priority=30,
                input_schema="plan_and_evidence_v1",
                output_schema="draft_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="AGENT",
                capability_name="critic_agent",
                intent="critic_solution",
                priority=35,
                input_schema="plan_and_evidence_v1",
                output_schema="critique_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="REDUCER",
                capability_name="final_reducer",
                intent="aggregate_final",
                priority=40,
                input_schema="final_input_v1",
                output_schema="final_result_v1",
                request_payload={"goal": goal},
            ),
        ])

        subtasks = ["retrieve_evidence", "merge_evidence", "draft_solution", "critic_solution", "aggregate_final"]
        # 收集实际 spawn 的工具类任务用于记录
        tool_tasks = [s.intent for s in spawn_specs if s.task_kind == "TOOL"]
        # 把工具任务插入前面
        for i, s in enumerate(spawn_specs):
            if s.task_kind == "TOOL":
                subtasks.insert(0, s.intent)

        return CapabilityResult(
            status="WAITING_CHILDREN",
            output_artifact={
                "artifact_type": "plan_v1",
                "payload": {
                    "goal": goal,
                    "subtasks": subtasks,
                    "llm_analysis": llm_analysis,
                },
            },
            spawn_specs=spawn_specs,
        )
