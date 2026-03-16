"""Capability 默认注册表。"""

from nanobot.agentloop.capabilities.base import CapabilityRegistry
from nanobot.agentloop.capabilities.agents.root import RootAgent
from nanobot.agentloop.capabilities.agents.planner import PlannerAgent
from nanobot.agentloop.capabilities.agents.drafter import DrafterAgent
from nanobot.agentloop.capabilities.agents.critic import CriticAgent
from nanobot.agentloop.capabilities.tools.search_tool import SearchTool
from nanobot.agentloop.capabilities.reducers import RetrieverGroupReducer, FinalReducer


def create_default_registry() -> CapabilityRegistry:
    """创建并注册默认 capabilities。"""
    registry = CapabilityRegistry()
    registry.register(RootAgent())
    registry.register(PlannerAgent())
    registry.register(DrafterAgent())
    registry.register(CriticAgent())
    registry.register(SearchTool())
    registry.register(RetrieverGroupReducer())
    registry.register(FinalReducer())
    return registry
