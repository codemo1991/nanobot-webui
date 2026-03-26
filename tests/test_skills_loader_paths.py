"""Tests for SkillsLoader path summaries (dir / workspace-relative)."""

from pathlib import Path

from nanobot.agent.skills import BUILTIN_SKILLS_DIR, SkillMetadata, SkillsLoader


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_skill_resource_dir_parent_of_skill_md() -> None:
    p = "/x/y/self-improving-agent/SKILL.md"
    assert SkillsLoader.skill_resource_dir(p).replace("\\", "/") == "/x/y/self-improving-agent"


def test_format_paths_relative_when_under_workspace(tmp_path: Path) -> None:
    ws = tmp_path
    skill_root = ws / "skills" / "demo-skill"
    skill_root.mkdir(parents=True)
    md = skill_root / "SKILL.md"
    md.write_text(
        '---\nname: demo-skill\ndescription: "x"\nmetadata: {"nanobot":{}}\n---\n\n# Hi\n',
        encoding="utf-8",
    )
    loader = SkillsLoader(ws)
    loader.refresh_cache()
    meta = loader.get_cached_metadata("demo-skill")
    assert meta is not None
    fmt = loader._format_skill_paths_for_summary(meta)
    assert "skills/demo-skill/SKILL.md" in fmt.replace("\\", "/")
    assert "dir: `skills/demo-skill`" in fmt.replace("\\", "/")


def test_build_skill_paths_index_includes_status() -> None:
    root = _repo_root()
    loader = SkillsLoader(root)
    loader.refresh_cache()
    if loader.get_cached_metadata("summarize") is None:
        return
    idx = loader.build_skill_paths_index(["summarize"])
    assert "summarize" in idx
    assert "dir:" in idx


def test_builtin_paths_resolve_when_workspace_is_repo_root() -> None:
    root = _repo_root()
    builtin_skill = BUILTIN_SKILLS_DIR / "summarize" / "SKILL.md"
    if not builtin_skill.exists():
        return
    loader = SkillsLoader(root)
    loader.refresh_cache()
    meta = SkillMetadata(
        name="summarize",
        description="d",
        path=str(builtin_skill.resolve()),
        source="builtin",
    )
    fmt = loader._format_skill_paths_for_summary(meta)
    assert "nanobot/skills/summarize" in fmt.replace("\\", "/")
    assert "dir:" in fmt
