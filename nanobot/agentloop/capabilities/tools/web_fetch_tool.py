"""WebFetch Tool：包装主 Agent 的 WebFetchTool，能力对等。"""

import json

from nanobot.agent.tools.web import WebFetchTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class WebFetchCapability(Capability):
    """抓取 URL 内容，产出 web_content_v1 artifact。"""

    name = "web_fetch_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        url = (request.get("url") or "").strip()
        if not url:
            return CapabilityResult(
                status="FAILED",
                error_code="WEB_FETCH_ERROR",
                error_message="url is required",
            )
        extract_mode = request.get("extractMode", "markdown")
        max_chars = request.get("maxChars")
        tool = WebFetchTool(max_chars=max_chars or 50000)
        try:
            result = await tool.execute(
                url=url,
                extractMode=extract_mode,
                maxChars=max_chars,
            )
            # 解析 JSON 检查顶层 error 字段，避免网页正文含 "error" 时误判
            if isinstance(result, str):
                try:
                    data = json.loads(result)
                    if isinstance(data, dict) and data.get("error"):
                        return CapabilityResult(
                            status="FAILED",
                            error_code="WEB_FETCH_ERROR",
                            error_message=str(data["error"])[:500],
                        )
                except json.JSONDecodeError:
                    pass
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "web_content_v1",
                    "payload": {"url": url, "content": result[:50000]},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="WEB_FETCH_ERROR",
                error_message=str(e),
            )
