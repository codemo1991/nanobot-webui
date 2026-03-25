"""基于工具参数的静态并行依赖分析，零延迟，无需 LLM。

判断策略（优先级由高到低）：
  1. message 工具 → 强制串行
  2. 写-写同路径 → 强制串行
  3. 写-读同路径 → 强制串行（数据竞争）
  4. 全为只读工具 → 可并行
  5. 全为后台工具（无本地文件副作用）→ 可并行
  6. 其余 → 信任主 LLM 批次决策（主 LLM 在同一批次返回即已隐含可并行判断）
"""

from typing import Any

WRITE_TOOLS = {"write_file", "edit_file", "exec", "spawn", "cron", "remember"}
READ_TOOLS = {"read_file", "list_dir", "web_search", "web_fetch"}
MUST_SERIAL_TOOLS = {"message"}
# 后台工具：无本地文件写副作用，可安全并行
BACKGROUND_TOOLS = {"exec", "spawn", "web_search", "web_fetch", "read_file", "list_dir"}


def _extract_paths(args: dict) -> set[str]:
    """提取工具参数中的文件路径（兼容不同工具的参数名）。"""
    paths = set()
    for k in ("path", "file_path", "target", "file"):
        v = args.get(k)
        if isinstance(v, str) and v:
            paths.add(v.strip())
    return paths


def analyze(tool_calls: list) -> dict[str, Any]:
    """
    分析工具调用间的并行依赖关系。

    Returns:
        {
            "can_parallel": bool,
            "groups": list[list],   # 单组时 = [tool_calls]，多组时组间串行、组内并行
            "reason": str,
        }
    """
    if len(tool_calls) <= 1:
        return {
            "can_parallel": False,
            "groups": [tool_calls] if tool_calls else [],
            "reason": "单个工具",
        }

    infos: list[tuple[str, dict]] = []
    for tc in tool_calls:
        name = tc.name if hasattr(tc, "name") else tc.get("name", "")
        args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        infos.append((name, args))

    names = [n for n, _ in infos]

    # ── 规则 1：message 工具强制串行 ──────────────────────────────────────────
    if any(n in MUST_SERIAL_TOOLS for n in names):
        return {
            "can_parallel": False,
            "groups": [tool_calls],
            "reason": "message 工具需串行（用户交互）",
        }

    # ── 规则 2 & 3：路径冲突检测 ─────────────────────────────────────────────
    write_paths: set[str] = set()
    read_paths: set[str] = set()

    for name, args in infos:
        paths = _extract_paths(args)
        if name in WRITE_TOOLS:
            # 写-写同路径冲突
            conflicts = paths & write_paths
            if conflicts:
                return {
                    "can_parallel": False,
                    "groups": [tool_calls],
                    "reason": f"写-写同路径冲突: {next(iter(conflicts))}",
                }
            write_paths |= paths
        elif name in READ_TOOLS:
            read_paths |= paths

    # 写-读同路径冲突（写入方尚未完成，读取方可能读到旧数据）
    rw_conflicts = write_paths & read_paths
    if rw_conflicts:
        return {
            "can_parallel": False,
            "groups": [tool_calls],
            "reason": f"写-读同路径数据竞争: {next(iter(rw_conflicts))}",
        }

    # ── 规则 4：纯读操作 ──────────────────────────────────────────────────────
    if all(n in READ_TOOLS for n in names):
        return {
            "can_parallel": True,
            "groups": [tool_calls],
            "reason": "纯读操作，无副作用冲突",
        }

    # ── 规则 5：全后台工具 ───────────────────────────────────────────────────
    if all(n in BACKGROUND_TOOLS for n in names):
        return {
            "can_parallel": True,
            "groups": [tool_calls],
            "reason": "全后台工具，无本地文件写依赖",
        }

    # ── 规则 6：信任主 LLM 批次决策 ─────────────────────────────────────────
    # 主 LLM 在单次响应中批量返回这些工具，已经过完整上下文推理，隐含可并行判断。
    # 二级 LLM 的信息量远不及主 LLM，不应覆盖此决策。
    return {
        "can_parallel": True,
        "groups": [tool_calls],
        "reason": "信任主 LLM 批次决策",
    }
