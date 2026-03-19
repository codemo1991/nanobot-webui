"""Capability 默认注册表。"""

from pathlib import Path

from nanobot.agentloop.capabilities.base import CapabilityRegistry
from nanobot.agentloop.capabilities.agents.root import RootAgent
from nanobot.agentloop.capabilities.agents.planner import PlannerAgent
from nanobot.agentloop.capabilities.agents.drafter import DrafterAgent
from nanobot.agentloop.capabilities.agents.critic import CriticAgent
from nanobot.agentloop.capabilities.tools.search_tool import SearchTool
from nanobot.agentloop.capabilities.tools.read_file_tool import ReadFileCapability
from nanobot.agentloop.capabilities.tools.list_dir_tool import ListDirCapability
from nanobot.agentloop.capabilities.tools.write_file_tool import WriteFileCapability
from nanobot.agentloop.capabilities.tools.edit_file_tool import EditFileCapability
from nanobot.agentloop.capabilities.tools.exec_tool import ExecCapability
from nanobot.agentloop.capabilities.tools.web_search_tool import WebSearchCapability
from nanobot.agentloop.capabilities.tools.web_fetch_tool import WebFetchCapability
from nanobot.agentloop.capabilities.reducers import RetrieverGroupReducer, FinalReducer


def create_default_registry(
    workspace: Path | None = None,
    brave_api_key: str | None = None,
) -> CapabilityRegistry:
    """创建并注册默认 capabilities，与主 Agent 能力对等。"""
    registry = CapabilityRegistry()
    # Agents
    registry.register(RootAgent())
    registry.register(PlannerAgent())
    registry.register(DrafterAgent())
    registry.register(CriticAgent())
    # Tools（与主 Agent 能力对等）
    registry.register(SearchTool())
    registry.register(ReadFileCapability())
    registry.register(ListDirCapability())
    registry.register(WriteFileCapability())
    registry.register(EditFileCapability())
    registry.register(ExecCapability())
    registry.register(WebSearchCapability(api_key=brave_api_key))
    registry.register(WebFetchCapability())
    # Reducers
    registry.register(RetrieverGroupReducer())
    registry.register(FinalReducer())
    return registry
