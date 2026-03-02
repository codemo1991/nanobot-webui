"""基于工具参数的并行依赖分析，无需 LLM。"""

from typing import Any

WRITE_TOOLS = {"write_file", "edit_file", "exec", "spawn", "claude_code", "cron", "remember"}
READ_TOOLS = {"read_file", "list_dir", "web_search", "web_fetch"}
MUST_SERIAL_TOOLS = {"message"}
BACKGROUND_TOOLS = {"exec", "spawn", "claude_code", "web_search", "web_fetch", "read_file", "list_dir"}


def _extract_paths(args: dict) -> set[str]:
    """提取工具参数中的文件路径。"""
    paths = set()
    for k in ("path", "file_path", "target", "file"):
        v = args.get(k)
        if isinstance(v, str) and v:
            paths.add(v.strip())
    return paths


def analyze(tool_calls: list) -> dict[str, Any]:
    """
    分析工具调用依赖。
    返回: {"can_parallel": bool, "groups": list, "reason": str, "need_llm": bool}
    """
    if len(tool_calls) <= 1:
        return {
            "can_parallel": False,
            "groups": [tool_calls] if tool_calls else [],
            "reason": "单个工具",
            "need_llm": False,
        }

    infos = []
    for tc in tool_calls:
        name = tc.name if hasattr(tc, "name") else tc.get("name", "")
        args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        infos.append((name, args))

    names = [n for n, _ in infos]

    # message 必须串行
    if any(n in MUST_SERIAL_TOOLS for n in names):
        return {"can_parallel": False, "groups": [tool_calls], "reason": "message 需串行", "need_llm": False}

    # 同文件写操作冲突
    write_paths = set()
    for name, args in infos:
        if name in WRITE_TOOLS:
            for p in _extract_paths(args):
                if p in write_paths:
                    return {"can_parallel": False, "groups": [tool_calls], "reason": "同文件写冲突", "need_llm": False}
                write_paths.add(p)

    # 仅读操作可并行
    if all(n in READ_TOOLS for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "仅读操作", "need_llm": False}

    # 均为后台型可并行
    if all(n in BACKGROUND_TOOLS for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "无依赖后台工具", "need_llm": False}

    # 无法确定，交 LLM
    return {"can_parallel": True, "groups": [tool_calls], "reason": "需 LLM 判断", "need_llm": True}
