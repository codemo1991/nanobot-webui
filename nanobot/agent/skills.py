"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

import yaml

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

MAX_DESCRIPTION_LENGTH = 80
MAX_SHORT_DESCRIPTION_LENGTH = 40


@dataclass
class SkillMetadata:
    """Cached skill metadata for efficient access."""
    name: str
    description: str
    short_description: str = ""
    keywords: list[str] = field(default_factory=list)
    category: str = ""
    available: bool = True
    missing_requirements: str = ""
    always: bool = False
    path: str = ""
    source: str = "builtin"
    emoji: str = ""


class SkillsLoader:
    """
    Loader for agent skills.
    
    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    
    Optimized with:
    - Metadata caching to avoid repeated file reads
    - Smart keyword matching for dynamic skill loading
    - Tiered output for token efficiency
    """
    
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self._metadata_cache: dict[str, SkillMetadata] = {}
        self._cache_valid = False

    @staticmethod
    def skill_resource_dir(skill_md_path: str) -> str:
        """Directory that contains SKILL.md; siblings include memory/, references/, templates/, etc."""
        if not skill_md_path:
            return ""
        return str(Path(skill_md_path).parent)

    def _format_skill_paths_for_summary(self, meta: SkillMetadata) -> str:
        """SKILL.md path plus parent dir; use paths relative to workspace when under workspace (shorter)."""
        if not meta.path:
            return ""
        root = self.skill_resource_dir(meta.path)
        if not root:
            return f"`{meta.path}`"

        ws = self.workspace.expanduser().resolve()
        try:
            rel_skill = Path(meta.path).resolve().relative_to(ws)
            rel_root = Path(root).resolve().relative_to(ws)
            return f"`{rel_skill.as_posix()}` | dir: `{rel_root.as_posix()}`"
        except ValueError:
            return f"`{meta.path}` | dir: `{root}`"

    def build_skill_paths_index(self, names: list[str]) -> str:
        """Markdown lines: skill name, SKILL.md path, and dir (for subagent / always-loaded skills)."""
        self._ensure_cache()
        lines: list[str] = []
        for name in names:
            meta = self._metadata_cache.get(name)
            if not meta or not meta.path:
                continue
            status = "✓" if meta.available else "✗"
            paths = self._format_skill_paths_for_summary(meta)
            lines.append(f"- **{name}** ({status}): {paths}")
        return "\n".join(lines)
    
    def _ensure_cache(self) -> None:
        """Build metadata cache if not valid."""
        if not self._cache_valid:
            self._build_metadata_cache()
    
    def _build_metadata_cache(self) -> None:
        """Build cache of all skill metadata."""
        self._metadata_cache.clear()
        
        skills_list = self._list_skill_dirs()
        for skill_info in skills_list:
            name = skill_info["name"]
            meta = self._parse_skill_file(name, skill_info)
            if meta:
                self._metadata_cache[name] = meta
        
        self._cache_valid = True
    
    def _list_skill_dirs(self) -> list[dict[str, str]]:
        """List all skill directories without filtering. Supports nested category paths."""
        skills = []
        seen_names: set[str] = set()

        def _collect(root: Path, source: str) -> None:
            if not root.exists():
                return
            for skill_file in root.rglob("SKILL.md"):
                skill_dir = skill_file.parent
                # Compute name as relative path from root to skill_dir
                try:
                    rel = skill_dir.relative_to(root).as_posix()
                    rel_parts = skill_dir.relative_to(root).parts
                except ValueError:
                    continue
                if not rel or rel in seen_names:
                    continue
                if any(p in {"references", "templates", "scripts", "assets"} for p in rel_parts):
                    continue
                seen_names.add(rel)
                skills.append({"name": rel, "path": str(skill_file), "source": source})

        _collect(self.workspace_skills, "workspace")
        _collect(self.builtin_skills, "builtin")
        return skills
    
    def _parse_skill_file(self, name: str, skill_info: dict) -> SkillMetadata | None:
        """Parse a skill file and return cached metadata."""
        content = self.load_skill(name)
        if not content:
            return None
        
        raw_meta = self._extract_frontmatter(content)
        nanobot_meta = self._parse_nanobot_metadata(raw_meta.get("metadata", ""))
        
        description = raw_meta.get("description", name)
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH - 3] + "..."
        
        short_desc = raw_meta.get("short_description", "")
        if not short_desc:
            short_desc = description[:MAX_SHORT_DESCRIPTION_LENGTH]
            if len(description) > MAX_SHORT_DESCRIPTION_LENGTH:
                short_desc = short_desc[:-3] + "..."
        
        raw_keywords = raw_meta.get("keywords") or raw_meta.get("triggers", "")
        keywords = self._parse_keywords(raw_keywords)
        
        skill_meta = nanobot_meta
        available = self._check_requirements(skill_meta)
        missing = self._get_missing_requirements(skill_meta) if not available else ""
        
        return SkillMetadata(
            name=name,
            description=description,
            short_description=short_desc,
            keywords=keywords,
            category=raw_meta.get("category", ""),
            available=available,
            missing_requirements=missing,
            always=bool(skill_meta.get("always") or raw_meta.get("always")),
            path=skill_info["path"],
            source=skill_info["source"],
            emoji=nanobot_meta.get("emoji", ""),
        )
    
    def _extract_frontmatter(self, content: str) -> dict:
        """Extract frontmatter as dict from content using YAML."""
        if not content.startswith("---"):
            return {}

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}

        yaml_text = match.group(1)
        try:
            metadata = yaml.safe_load(yaml_text) or {}
            if not isinstance(metadata, dict):
                return {}
            return metadata
        except yaml.YAMLError:
            # Fallback to simple line parser for malformed frontmatter
            metadata = {}
            for line in yaml_text.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip().strip('"\'')
            return metadata
    
    def _parse_keywords(self, keywords_str: str | list[str]) -> list[str]:
        """Parse keywords from comma-separated string or list."""
        if not keywords_str:
            return []
        if isinstance(keywords_str, list):
            return [str(k).strip().lower() for k in keywords_str if str(k).strip()]
        return [k.strip().lower() for k in keywords_str.split(",") if k.strip()]
    
    def refresh_cache(self) -> None:
        """Force refresh of metadata cache."""
        self._cache_valid = False
        self._ensure_cache()
    
    def get_cached_metadata(self, name: str) -> SkillMetadata | None:
        """Get cached metadata for a skill."""
        self._ensure_cache()
        return self._metadata_cache.get(name)
    
    def get_all_cached_metadata(self) -> dict[str, SkillMetadata]:
        """Get all cached metadata."""
        self._ensure_cache()
        return self._metadata_cache.copy()
    
    def match_skills_by_keywords(self, message: str, top_n: int = 5) -> list[str]:
        """
        Match skills by keywords in the message.
        
        Args:
            message: User message to match against.
            top_n: Maximum number of skills to return.
        
        Returns:
            List of matched skill names sorted by relevance.
        """
        self._ensure_cache()
        
        message_lower = message.lower()
        message_words = set(re.findall(r'\w+', message_lower))
        
        scored_skills: list[tuple[str, int]] = []
        
        for name, meta in self._metadata_cache.items():
            if not meta.available:
                continue
            
            score = 0
            for keyword in meta.keywords:
                if keyword in message_lower:
                    score += 10
                elif keyword in message_words:
                    score += 5
            
            if meta.category and meta.category.lower() in message_lower:
                score += 3
            
            if score > 0:
                scored_skills.append((name, score))
        
        scored_skills.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored_skills[:top_n]]
    
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.
        
        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
        
        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        self._ensure_cache()
        
        skills = []
        for name, meta in self._metadata_cache.items():
            if filter_unavailable and not meta.available:
                continue
            skills.append({
                "name": name,
                "path": meta.path,
                "source": meta.source
            })
        
        return skills
    
    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.
        
        Args:
            name: Skill name (directory name).
        
        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")
        
        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")
        
        return None
    
    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.
        
        Args:
            skill_names: List of skill names to load.
        
        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        
        return "\n\n---\n\n".join(parts) if parts else ""
    
    def build_skills_summary(
        self,
        level: int = 0,
        matched_skills: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Build a compact summary of all skills with tiered output.
        
        Args:
            level: Output detail level (0=minimal, 1=short, 2=full).
            matched_skills: Skills that matched user message (for prioritization).
            max_tokens: Maximum tokens for the summary.
        
        Returns:
            Compact markdown-formatted skills summary.
        """
        self._ensure_cache()
        
        if not self._metadata_cache:
            return ""
        
        matched_set = set(matched_skills or [])
        
        sorted_skills = sorted(
            self._metadata_cache.items(),
            key=lambda x: (x[0] in matched_set, x[1].always, x[0]),
            reverse=True
        )
        
        lines = []
        current_tokens = 0
        
        for name, meta in sorted_skills:
            status = "✓" if meta.available else "✗"
            emoji = f"{meta.emoji} " if meta.emoji else ""
            paths = self._format_skill_paths_for_summary(meta)
            
            if level == 0:
                line = f"- **{name}**: {meta.short_description} ({status}) — {paths}"
            elif level == 1:
                line = f"- **{emoji}{name}**: {meta.description} ({status}) — {paths}"
            else:
                line = f"- **{emoji}{name}**: {meta.description} ({status}) — {paths}"
            
            if not meta.available and meta.missing_requirements:
                line += f" — requires: {meta.missing_requirements}"
            
            line_tokens = len(line) // 4
            if max_tokens and current_tokens + line_tokens > max_tokens:
                break
            
            lines.append(line)
            current_tokens += line_tokens
        
        return "\n".join(lines)
    
    def build_dynamic_summary(self, user_message: str, max_skills: int = 8) -> str:
        """
        Build a dynamic skills summary based on user message.
        
        Intelligently shows more detail for relevant skills.
        
        Args:
            user_message: The user's message for keyword matching.
            max_skills: Maximum number of skills to show.
        
        Returns:
            Optimized skills summary.
        """
        self._ensure_cache()
        
        if not self._metadata_cache:
            return ""
        
        matched = self.match_skills_by_keywords(user_message, top_n=3)
        always_skills = [name for name, meta in self._metadata_cache.items() if meta.always and meta.available]
        
        priority_skills = set(matched) | set(always_skills)
        
        lines = []
        shown_count = 0
        
        for name in priority_skills:
            meta = self._metadata_cache.get(name)
            if not meta:
                continue
            
            status = "✓" if meta.available else "✗"
            emoji = f"{meta.emoji} " if meta.emoji else ""
            paths = self._format_skill_paths_for_summary(meta)
            line = f"- **{emoji}{name}**: {meta.description} ({status}) — {paths}"
            lines.append(line)
            shown_count += 1
        
        other_skills = [
            (name, meta) for name, meta in self._metadata_cache.items()
            if name not in priority_skills and meta.available
        ]
        
        for name, meta in other_skills:
            if shown_count >= max_skills:
                break
            
            status = "✓" if meta.available else "✗"
            paths = self._format_skill_paths_for_summary(meta)
            line = f"- **{name}**: {meta.short_description} ({status}) — {paths}"
            lines.append(line)
            shown_count += 1
        
        return "\n".join(lines)
    
    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)
    
    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name
    
    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
    
    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """Parse nanobot metadata JSON from frontmatter."""
        try:
            data = json.loads(raw)
            return data.get("nanobot", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True
    
    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))
    
    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        self._ensure_cache()
        return [
            name for name, meta in self._metadata_cache.items()
            if meta.always and meta.available
        ]

    def get_active_skills_for_message(self, user_message: str, max_skills: int = 8) -> list[str]:
        """Return the list of skill names that would be loaded into system prompt for this message."""
        self._ensure_cache()
        if not self._metadata_cache:
            return []
        matched = self.match_skills_by_keywords(user_message, top_n=3)
        always_skills = [name for name, meta in self._metadata_cache.items() if meta.always and meta.available]
        # preserve order, dedup
        seen: set[str] = set()
        active: list[str] = []
        for name in list(matched) + list(always_skills):
            if name in seen:
                continue
            seen.add(name)
            meta = self._metadata_cache.get(name)
            if meta and meta.available:
                active.append(name)
        # fill up to max_skills with other available skills
        other_skills = [
            name for name, meta in self._metadata_cache.items()
            if name not in seen and meta.available
        ]
        while len(active) < max_skills and other_skills:
            active.append(other_skills.pop(0))
        return active
    
    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content:
            return None
        return self._extract_frontmatter(content)
