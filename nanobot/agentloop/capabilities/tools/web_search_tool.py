"""WebSearch Tool：包装主 Agent 的 WebSearchTool，能力对等。"""

import os

from nanobot.agent.tools.web import WebSearchTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class WebSearchCapability(Capability):
    """Web 搜索，产出 search_result_v1 artifact。"""

    name = "web_search_tool"
    kind = "tool"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("BRAVE_API_KEY", "")

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        query = (request.get("query") or "").strip()
        if not query:
            return CapabilityResult(
                status="FAILED",
                error_code="WEB_SEARCH_ERROR",
                error_message="query is required",
            )
        count = request.get("count", 5)
        tool = WebSearchTool(api_key=self._api_key, max_results=count or 5)
        try:
            result = await tool.execute(query=query, count=count)
            if result.startswith("Error:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="WEB_SEARCH_ERROR",
                    error_message=result,
                )
            # WebSearchTool 返回格式：每项为 "1. 标题" 行，下一行可能为 URL；无此格式时回退为单条
            items = []
            lines = result.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line and len(line) >= 2 and line[0].isdigit() and line[1] == ".":
                    title = line.split(".", 1)[1].strip()[:200] if "." in line[:4] else line[:200]
                    url = ""
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("http"):
                        url = lines[i + 1].strip()
                        i += 1
                    items.append({"title": title, "url": url, "score": 0.9})
                i += 1
            if not items:
                items = [{"title": result[:200], "url": "", "score": 0.5}]
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "search_result_v1",
                    "payload": {"query": query, "raw": result, "items": items},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="WEB_SEARCH_ERROR",
                error_message=str(e),
            )
