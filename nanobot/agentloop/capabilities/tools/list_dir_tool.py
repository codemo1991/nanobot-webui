"""ListDir Tool：包装主 Agent 的 ListDirTool，能力对等。"""

from nanobot.agent.tools.filesystem import ListDirTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class ListDirCapability(Capability):
    """列出目录内容，产出 dir_listing_v1 artifact。"""

    name = "list_dir_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        path = (request.get("path") or "").strip()
        if not path:
            return CapabilityResult(
                status="FAILED",
                error_code="LIST_DIR_ERROR",
                error_message="path is required",
            )
        workspace = context.get("workspace")
        tool = ListDirTool(workspace=workspace, restrict_to_workspace=True)
        try:
            result = await tool.execute(path=path)
            if result.startswith("Error:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="LIST_DIR_ERROR",
                    error_message=result,
                )
            entries = [line.strip() for line in result.split("\n") if line.strip()]
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "dir_listing_v1",
                    "payload": {"path": path, "entries": entries, "raw": result},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="LIST_DIR_ERROR",
                error_message=str(e),
            )
