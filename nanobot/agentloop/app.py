"""AgentLoop 微内核 CLI 入口。"""

import asyncio
import json
from pathlib import Path

from nanobot.agentloop.kernel.kernel import create_kernel
from nanobot.agentloop.kernel.artifact_repo import get_artifact_payload


async def main(user_input: str = "设计 agentloop 微内核", workspace: Path | None = None) -> str | None:
    """运行 AgentLoop 示例，返回最终结果 JSON 或 None。"""
    kernel = create_kernel(workspace=workspace)

    trace_id, root_task_id = await kernel.submit(user_input)

    await kernel.run_until_done(trace_id, worker_count=4, timeout_seconds=30)

    # Fix #8: 先查 result_artifact_id，再通过 get_artifact_payload 读取 payload。
    # 原实现直接 JOIN 读 payload_text，当 artifact 超过 64KB 落盘（storage_kind='FILE'）
    # 时 payload_text 为 NULL，会静默丢失结果。
    row = kernel.conn.execute(
        """
        SELECT t.result_artifact_id
        FROM agentloop_tasks t
        WHERE t.trace_id = ?
          AND t.output_schema = 'final_result_v1'
          AND t.state = 'DONE'
        ORDER BY t.finished_at DESC
        LIMIT 1
        """,
        (trace_id,),
    ).fetchone()

    if row and row["result_artifact_id"]:
        payload = get_artifact_payload(kernel.conn, row["result_artifact_id"])
        if payload is not None:
            return json.dumps(payload, ensure_ascii=False)
    return None


def run_cli():
    """命令行入口。"""
    import sys
    user_input = sys.argv[1] if len(sys.argv) > 1 else "设计 agentloop 微内核"
    workspace = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    result = asyncio.run(main(user_input=user_input, workspace=workspace))
    if result:
        print(json.dumps(json.loads(result), ensure_ascii=False, indent=2))
    else:
        print("未获取到最终结果")


if __name__ == "__main__":
    run_cli()
