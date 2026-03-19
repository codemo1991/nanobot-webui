"""Planner Agent：规划任务并 spawn 子任务。"""

import re

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult, TaskSpec


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


class PlannerAgent(Capability):
    """规划 agent，根据 goal/attempted_steps/initial_artifacts 动态 spawn 子任务。"""

    name = "planner_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        goal = request.get("user_goal", request.get("goal", ""))
        attempted = request.get("attempted_steps", [])
        initial_keys = request.get("initial_artifacts_keys", [])

        spawn_specs: list[TaskSpec] = []

        search_cap = "web_search_tool"
        if _should_add_web_search(goal, initial_keys):
            # 仅 spawn 一个 web_search（API 已通过 count 返回多条结果），避免重复请求
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
        if add_read:
            subtasks.insert(0, "read_file")
        if add_exec:
            subtasks.insert(0, "exec")

        return CapabilityResult(
            status="WAITING_CHILDREN",
            output_artifact={
                "artifact_type": "plan_v1",
                "payload": {
                    "goal": goal,
                    "subtasks": subtasks,
                },
            },
            spawn_specs=spawn_specs,
        )
