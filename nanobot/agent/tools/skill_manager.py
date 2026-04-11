"""Skill management tool — create, edit, delete, patch, and write files for workspace skills."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from nanobot.agent.tools.base import Tool

# ---- 常量 ----
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-\.]{0,63}$")
MAX_DESCRIPTION_LENGTH = 80
MAX_SKILL_CONTENT_CHARS = 50_000

VALID_ACTIONS = ["create", "edit", "delete", "patch", "write_file"]

# ---- 安全扫描 ----
_THREAT_PATTERNS = [
    (re.compile(r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)', re.I), "exfil_curl"),
    (re.compile(r'ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions', re.I), "prompt_injection"),
    (re.compile(r'you\s+are\s+now\s+', re.I), "role_hijack"),
    (re.compile(r'do\s+not\s+tell\s+the\s+user', re.I), "deception_hide"),
    (re.compile(r'crontab\b', re.I), "persistence"),
    (re.compile(r'authorized_keys', re.I), "ssh_backdoor"),
    (re.compile(r'\bnc\s+-[lp]', re.I), "reverse_shell"),
    (re.compile(r'\$HOME/\.ssh|\~/\.ssh', re.I), "ssh_access"),
]

_INVISIBLE_CHARS = {'\u200b', '\u200c', '\u200d', '\u2060', '\ufeff', '\u202a', '\u202b', '\u202c', '\u202d', '\u202e'}


def _validate_name(name: str) -> Optional[str]:
    if not name:
        return "Skill name cannot be empty."
    if not VALID_NAME_RE.match(name):
        return (
            "Invalid name. Use lowercase letters, numbers, hyphens, dots, and underscores. "
            "Must start with alphanumeric. Max 64 characters."
        )
    return None


def _validate_frontmatter(content: str) -> Optional[str]:
    """Validate SKILL.md content has proper frontmatter. Returns error or None."""
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---)."
    end_match = re.search(r'\n---\s*\n', content[3:])
    if not end_match:
        return "SKILL.md frontmatter is not closed."
    yaml_content = content[3:end_match.start() + 3]
    try:
        import yaml
        parsed = yaml.safe_load(yaml_content)
    except Exception:
        return "YAML frontmatter parse error."
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping."
    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[end_match.end() + 3:].strip()
    if not body:
        return "SKILL.md must have content after the frontmatter."
    return None


def _validate_content_size(content: str) -> Optional[str]:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return f"Content exceeds {MAX_SKILL_CONTENT_CHARS:,} characters."
    return None


def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write text using temp file + os.replace."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(file_path.parent), prefix=f".{file_path.name}.tmp.", suffix="")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _security_scan(content: str) -> Optional[str]:
    """Scan for threat patterns. Returns error string or None if clean."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: invisible unicode character detected."
    for pattern, pid in _THREAT_PATTERNS:
        if pattern.search(content):
            return f"Blocked: threat pattern '{pid}' detected."
    return None


def _skills_dir(workspace: Path) -> Path:
    return workspace / "skills"


def _skill_path(workspace: Path, name: str) -> Path:
    return _skills_dir(workspace) / name / "SKILL.md"


def _skill_exists(workspace: Path, name: str) -> bool:
    return _skill_path(workspace, name).exists()


def _create_skill(name: str, content: str, workspace: Path) -> dict[str, Any]:
    err = _validate_name(name)
    if err:
        return {"success": False, "error": err}
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}
    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}
    if _skill_exists(workspace, name):
        return {"success": False, "error": f"A skill named '{name}' already exists."}

    skill_md = _skill_path(workspace, name)
    _atomic_write_text(skill_md, content)

    scan_error = _security_scan(content)
    if scan_error:
        shutil.rmtree(skill_md.parent, ignore_errors=True)
        return {"success": False, "error": scan_error}

    return {"success": True, "message": f"Skill '{name}' created.", "path": str(skill_md)}


def _edit_skill(name: str, content: str, workspace: Path) -> dict[str, Any]:
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}
    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}
    if not _skill_exists(workspace, name):
        return {"success": False, "error": f"Skill '{name}' not found."}

    skill_md = _skill_path(workspace, name)
    original = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
    _atomic_write_text(skill_md, content)

    scan_error = _security_scan(content)
    if scan_error:
        if original is not None:
            _atomic_write_text(skill_md, original)
        return {"success": False, "error": scan_error}

    return {"success": True, "message": f"Skill '{name}' updated.", "path": str(skill_md)}


def _delete_skill(name: str, workspace: Path) -> dict[str, Any]:
    err = _validate_name(name)
    if err:
        return {"success": False, "error": err}
    skill_dir = _skills_dir(workspace) / name
    if not skill_dir.exists():
        return {"success": False, "error": f"Skill '{name}' not found."}
    shutil.rmtree(skill_dir)
    return {"success": True, "message": f"Skill '{name}' deleted."}


def _patch_skill(name: str, old_string: str, new_string: str, workspace: Path) -> dict[str, Any]:
    if not old_string:
        return {"success": False, "error": "old_string is required for patch."}
    if not _skill_exists(workspace, name):
        return {"success": False, "error": f"Skill '{name}' not found."}
    skill_md = _skill_path(workspace, name)
    original = skill_md.read_text(encoding="utf-8")
    if old_string not in original:
        return {"success": False, "error": "old_string not found in SKILL.md."}
    new_content = original.replace(old_string, new_string, 1)
    scan_error = _security_scan(new_content)
    if scan_error:
        return {"success": False, "error": scan_error}
    _atomic_write_text(skill_md, new_content)
    return {"success": True, "message": f"Skill '{name}' patched."}


def _write_skill_file(name: str, file_path: str, file_content: str, workspace: Path) -> dict[str, Any]:
    if not _skill_exists(workspace, name):
        return {"success": False, "error": f"Skill '{name}' not found."}
    if ".." in file_path.split(os.sep):
        return {"success": False, "error": "Path traversal not allowed."}
    target = _skills_dir(workspace) / name / file_path
    try:
        target.relative_to((_skills_dir(workspace) / name).resolve())
    except ValueError:
        return {"success": False, "error": "File must be inside skill directory."}
    _atomic_write_text(target, file_content)
    return {"success": True, "message": f"File '{file_path}' written.", "path": str(target)}


class SkillManagerTool(Tool):
    """Tool for creating, editing, deleting, and patching workspace skills."""

    def __init__(self, workspace: Path | str, context: "ContextBuilder | None" = None):
        super().__init__()
        self.workspace = Path(workspace).resolve()
        self._context = context

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "创建、编辑、删除工作区内的 Skill（存储在 {workspace}/skills/）。\n"
            "触发时机：完成复杂任务（5+工具调用）、修复错误、发现可复用工作流。\n"
            "动作：create（新建）/ edit（全文替换）/ delete（删除）/ patch（局部替换）/ write_file（辅助文件）。"
        ).format(workspace=str(self.workspace))

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": VALID_ACTIONS,
                    "description": "操作类型"
                },
                "name": {
                    "type": "string",
                    "description": "Skill 名称（小写、字母数字、-_. ，最大64字符）"
                },
                "content": {
                    "type": "string",
                    "description": "SKILL.md 内容（create/edit 必须包含 YAML frontmatter）"
                },
                "file_path": {
                    "type": "string",
                    "description": "辅助文件路径（write_file 时必填，如 'references/example.md'）"
                },
                "file_content": {
                    "type": "string",
                    "description": "辅助文件内容（write_file 时必填）"
                },
                "old_string": {
                    "type": "string",
                    "description": "patch 的目标字符串"
                },
                "new_string": {
                    "type": "string",
                    "description": "patch 的替换字符串"
                },
            },
            "required": ["action", "name"]
        }

    async def execute(self, action: str, name: str, content: str = None, file_path: str = None,
                      file_content: str = None, old_string: str = None, new_string: str = None, **kwargs) -> str:
        if action == "create":
            if not content:
                return json.dumps({"success": False, "error": "content is required for create."})
            result = _create_skill(name, content, self.workspace)
        elif action == "edit":
            if not content:
                return json.dumps({"success": False, "error": "content is required for edit."})
            result = _edit_skill(name, content, self.workspace)
        elif action == "delete":
            result = _delete_skill(name, self.workspace)
        elif action == "patch":
            result = _patch_skill(name, old_string or "", new_string or "", self.workspace)
        elif action == "write_file":
            if not file_path:
                return json.dumps({"success": False, "error": "file_path is required for write_file."})
            if file_content is None:
                return json.dumps({"success": False, "error": "file_content is required for write_file."})
            result = _write_skill_file(name, file_path, file_content, self.workspace)
        else:
            return json.dumps({"success": False, "error": f"Unknown action '{action}'."})

        # 成功后刷新缓存
        if result.get("success") and self._context is not None:
            self._context.skills.refresh_cache()
            self._context.invalidate_skill_snapshot()

        return json.dumps(result, ensure_ascii=False)
