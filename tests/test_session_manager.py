from pathlib import Path

from nanobot.session.manager import SessionManager


def test_session_manager_sqlite_persistence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = SessionManager(workspace=tmp_path)
    session = manager.get_or_create("cli:default")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")
    manager.save(session)

    reloaded_manager = SessionManager(workspace=tmp_path)
    reloaded = reloaded_manager.get_or_create("cli:default")

    assert len(reloaded.messages) == 2
    assert reloaded.get_history() == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    sessions = reloaded_manager.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["key"] == "cli:default"
    assert sessions[0]["message_count"] == 2


def test_session_manager_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = SessionManager(workspace=tmp_path)
    session = manager.get_or_create("cli:to-delete")
    session.add_message("user", "bye")
    manager.save(session)

    assert manager.delete("cli:to-delete") is True
    assert manager.delete("cli:to-delete") is False
    assert manager.list_sessions() == []
