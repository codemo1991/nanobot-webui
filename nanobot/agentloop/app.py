"""AgentLoop 微内核 CLI 入口。"""

import asyncio
import json
from pathlib import Path

from nanobot.agentloop.kernel.kernel import create_kernel


async def main(user_input: str = "设计 agentloop 微内核", workspace: Path | None = None) -> str | None:
    """运行 AgentLoop 示例，返回最终结果 JSON 或 None。"""
    kernel = create_kernel(workspace=workspace)

    trace_id, root_task_id = await kernel.submit(user_input)

    await kernel.run_until_done(trace_id, worker_count=4, timeout_seconds=30)

    row = kernel.conn.execute(
        """
        SELECT a.payload_text
        FROM agentloop_tasks t
        JOIN agentloop_artifacts a ON a.artifact_id = t.result_artifact_id
        WHERE t.trace_id = ?
          AND t.output_schema = 'final_result_v1'
          AND t.state = 'DONE'
        ORDER BY t.finished_at DESC
        LIMIT 1
        """,
        (trace_id,),
    ).fetchone()

    if row:
        return row["payload_text"]
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
