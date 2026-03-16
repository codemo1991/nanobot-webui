"""Search Tool：模拟检索。"""

import asyncio

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class SearchTool(Capability):
    """搜索工具，模拟返回检索结果。"""

    name = "search_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        query = request.get("query", "")
        slot = request.get("slot", 0)

        await asyncio.sleep(0.05)

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "search_result_v1",
                "payload": {
                    "query": query,
                    "slot": slot,
                    "items": [
                        {"title": f"doc-{slot}-1", "score": 0.91},
                        {"title": f"doc-{slot}-2", "score": 0.88},
                    ],
                },
            },
        )
