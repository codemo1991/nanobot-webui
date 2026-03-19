"""ReadFile Tool：包装主 Agent 的 ReadFileTool，能力对等。"""

from nanobot.agent.tools.filesystem import ReadFileTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class ReadFileCapability(Capability):
    """读取文件，产出 doc_content_v1 artifact。"""

    name = "read_file_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        path = (request.get("path") or "").strip()
        if not path:
            return CapabilityResult(
                status="FAILED",
                error_code="READ_FILE_ERROR",
                error_message="path is required",
            )
        workspace = context.get("workspace")
        tool = ReadFileTool(workspace=workspace, restrict_to_workspace=True)
        try:
            content = await tool.execute(path=path)
            if content.startswith("Error:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="READ_FILE_ERROR",
                    error_message=content,
                )
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "doc_content_v1",
                    "payload": {"path": path, "content": content},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="READ_FILE_ERROR",
                error_message=str(e),
            )
