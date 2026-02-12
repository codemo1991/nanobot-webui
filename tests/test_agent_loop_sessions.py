from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse


class FakeProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(messages)
        last = messages[-1]["content"]
        return LLMResponse(content=f"echo:{last}")

    def get_default_model(self) -> str:
        return "fake-model"


@pytest.mark.asyncio
async def test_process_direct_respects_session_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    provider = FakeProvider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        max_iterations=2,
    )

    first = await loop.process_direct("first", session_key="cli:alpha")
    second = await loop.process_direct("second", session_key="cli:alpha")
    third = await loop.process_direct("third", session_key="cli:beta")

    assert first == "echo:first"
    assert second == "echo:second"
    assert third == "echo:third"

    second_call = provider.calls[1]
    assert any(msg["role"] == "user" and msg["content"] == "first" for msg in second_call)
    assert any(msg["role"] == "assistant" and msg["content"] == "echo:first" for msg in second_call)

    third_call = provider.calls[2]
    assert not any(msg["role"] == "user" and msg["content"] == "first" for msg in third_call)
