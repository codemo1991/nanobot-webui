"""Tests for self-improvement SQLite scope (self_improve)."""

import asyncio
from pathlib import Path

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.persist_self_improvement import PersistSelfImprovementTool
from nanobot.storage.memory_repository import (
    SCOPE_SELF_IMPROVE,
    SELF_IMPROVE_CONTENT_MAX_CHARS,
    MemoryRepository,
    reset_memory_repository,
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture(autouse=True)
def _reset_repo_cache(ws: Path) -> None:
    db = MemoryRepository.get_workspace_db_path(ws)
    MemoryRepository.clear_instance(db)
    reset_memory_repository()
    yield
    MemoryRepository.clear_instance(db)
    reset_memory_repository()


def test_upsert_self_improve_same_source_id_replaces(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)

    i1 = repo.upsert_self_improve_memory(
        content="first",
        source_type="self_improve_pattern",
        source_id="pat-test-001",
        agent_id=None,
    )
    i2 = repo.upsert_self_improve_memory(
        content="second",
        source_type="self_improve_pattern",
        source_id="pat-test-001",
        agent_id=None,
    )
    assert i2 != i1
    rows = repo.get_memories(agent_id=None, scope=SCOPE_SELF_IMPROVE, limit=50)
    assert len(rows) == 1
    assert rows[0]["content"] == "second"
    assert rows[0]["source_type"] == "self_improve_pattern"


def test_replace_global_does_not_delete_self_improve(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)

    repo.append_memory("global a", agent_id=None, scope="global")
    repo.upsert_self_improve_memory(
        content="lesson",
        source_type="self_improve_episode",
        source_id="ep-001",
        agent_id=None,
    )

    repo.replace_memories(
        entries=[("2025-01-01 00:00", "only global now")],
        agent_id=None,
        scope="global",
    )

    g = repo.get_memories(agent_id=None, scope="global", limit=10)
    si = repo.get_memories(agent_id=None, scope=SCOPE_SELF_IMPROVE, limit=10)
    assert len(g) == 1
    assert len(si) == 1
    assert si[0]["content"] == "lesson"


def test_get_memory_context_includes_self_improve(ws: Path) -> None:
    ws.mkdir(parents=True)
    store = MemoryStore(ws, agent_id=None)
    repo = store.get_repository()
    repo.upsert_self_improve_memory(
        content="总是先跑 impact 再改公共 API",
        source_type="self_improve_pattern",
        source_id="pat-ctx-1",
        agent_id=None,
    )
    ctx = store.get_memory_context()
    assert "自我改进沉淀" in ctx
    assert "self_improve" in ctx
    assert "总是先跑 impact" in ctx


def test_invalid_source_type_raises(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)
    with pytest.raises(ValueError, match="source_type"):
        repo.upsert_self_improve_memory(
            content="x",
            source_type="invalid",
            source_id="id1",
            agent_id=None,
        )


def test_same_source_id_different_types_two_rows(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)
    repo.upsert_self_improve_memory(
        content="pattern body",
        source_type="self_improve_pattern",
        source_id="shared-1",
        agent_id=None,
    )
    repo.upsert_self_improve_memory(
        content="episode body",
        source_type="self_improve_episode",
        source_id="shared-1",
        agent_id=None,
    )
    rows = repo.get_memories(agent_id=None, scope=SCOPE_SELF_IMPROVE, limit=50)
    assert len(rows) == 2


def test_upsert_preserves_created_at(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)
    repo.upsert_self_improve_memory(
        content="v1",
        source_type="self_improve_pattern",
        source_id="pat-preserve",
        agent_id=None,
    )
    first = repo.get_memories(agent_id=None, scope=SCOPE_SELF_IMPROVE, limit=50)[0][
        "created_at"
    ]
    repo.upsert_self_improve_memory(
        content="v2",
        source_type="self_improve_pattern",
        source_id="pat-preserve",
        agent_id=None,
    )
    second = repo.get_memories(agent_id=None, scope=SCOPE_SELF_IMPROVE, limit=50)[0][
        "created_at"
    ]
    assert first == second


def test_content_exceeds_max_raises(ws: Path) -> None:
    db_path = MemoryRepository.get_workspace_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repo = MemoryRepository(db_path)
    with pytest.raises(ValueError, match="max length"):
        repo.upsert_self_improve_memory(
            content="x" * (SELF_IMPROVE_CONTENT_MAX_CHARS + 1),
            source_type="self_improve_pattern",
            source_id="big",
            agent_id=None,
        )


def test_persist_tool_returns_friendly_error(ws: Path) -> None:
    ws.mkdir(parents=True)
    tool = PersistSelfImprovementTool(workspace=ws)
    msg = asyncio.run(
        tool.execute(
            content="",
            source_type="self_improve_pattern",
            source_id="e1",
        )
    )
    assert "错误" in msg


def test_search_includes_self_improve_scopes(ws: Path) -> None:
    ws.mkdir(parents=True)
    store = MemoryStore(ws, agent_id=None)
    repo = store.get_repository()
    repo.append_memory("global keyword_alpha uniquemark", agent_id=None, scope="global")
    repo.upsert_self_improve_memory(
        content="si uniquemark beta",
        source_type="self_improve_pattern",
        source_id="pat-s",
        agent_id=None,
    )
    hits = store.search("uniquemark", limit=10)
    scopes = {h["scope"] for h in hits}
    assert "global" in scopes
    assert SCOPE_SELF_IMPROVE in scopes
