"""Capability 基类与 Registry。"""

from abc import ABC, abstractmethod
from typing import Any

from nanobot.agentloop.kernel.models import CapabilityResult


class Capability(ABC):
    """能力抽象基类。"""

    name: str
    kind: str  # agent / tool / reducer

    @abstractmethod
    async def invoke(self, request: dict[str, Any], context: dict[str, Any]) -> CapabilityResult:
        """执行能力，返回结果。"""
        raise NotImplementedError


class CapabilityRegistry:
    """能力注册表。"""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        """注册能力。"""
        self._caps[capability.name] = capability

    def get(self, name: str) -> Capability:
        """获取能力。"""
        if name not in self._caps:
            raise KeyError(f"Capability not found: {name}")
        return self._caps[name]
