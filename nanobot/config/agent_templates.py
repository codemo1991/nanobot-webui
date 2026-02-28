"""Agent template management for configurable subagents."""

import json
import threading
import yaml
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from nanobot.storage.agent_template_repository import AgentTemplateRepository
from nanobot.config.builtin_templates_data import (
    DEFAULT_BUILTIN_TEMPLATES,
    VALID_TOOLS,
)


@dataclass
class AgentTemplateConfig:
    """Configuration for a subagent template."""

    name: str
    description: str
    tools: list[str]
    rules: list[str]
    system_prompt: str
    is_system: bool = False  # True = system built-in, False = user created
    model: Optional[str] = None  # Optional: use specific model for this template
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    enabled: bool = True
    workspace_path: str = ""  # For multi-workspace isolation

    @property
    def is_builtin(self) -> bool:
        """Check if this is a system built-in template."""
        return self.is_system

    @property
    def is_editable(self) -> bool:
        """Check if this template can be edited."""
        return True  # All templates are editable via UI

    @property
    def is_deletable(self) -> bool:
        """Check if this template can be deleted."""
        return not self.is_system  # Only user-created templates can be deleted

    def to_dict(self) -> dict:
        """Convert to dictionary (excluding source/timestamps for export)."""
        result = {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "rules": self.rules,
            "system_prompt": self.system_prompt,
        }
        if self.model:
            result["model"] = self.model
        return result

    def to_json(self) -> str:
        """Convert to JSON string for storage."""
        return json.dumps({
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "rules": self.rules,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str, is_system: bool = False, workspace_path: str = "") -> "AgentTemplateConfig":
        """Create from JSON string."""
        data = json.loads(json_str)
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            tools=data.get("tools", []),
            rules=data.get("rules", []),
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model"),
            is_system=is_system,
            enabled=data.get("enabled", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            workspace_path=workspace_path,
        )


class AgentTemplateManager:
    """
    Manages agent templates with support for:
    - Built-in default templates (read-only, stored in SQLite)
    - User-defined templates (stored in SQLite)
    - YAML import/export
    - Hot reload
    """

    def __init__(self, workspace: Path):
        from nanobot.storage import memory_repository

        self.workspace = workspace
        self.workspace_path = str(workspace)

        # Use same database path logic as the system
        # Prefer workspace database, otherwise use default database
        workspace_db_path = memory_repository.get_workspace_db_path(workspace)
        if workspace_db_path.exists():
            db_path = workspace_db_path
        else:
            db_path = memory_repository.get_default_db_path()

        # In-memory cache
        self._templates: dict[str, AgentTemplateConfig] = {}
        self._lock = threading.RLock()

        # Repository for all templates (SQLite)
        self._repo = AgentTemplateRepository(db_path)

        # Initialize builtin templates and load all
        self._init_and_load()

    def _init_and_load(self) -> None:
        """Initialize builtin templates if needed and load all templates."""
        with self._lock:
            self._templates.clear()

            # 1. Initialize builtin templates if not exist in database
            self._init_builtin_templates()

            # 2. Load all templates from database
            self._load_all_templates()

    def _init_builtin_templates(self) -> None:
        """Initialize builtin templates in SQLite if not exist."""
        try:
            # Get existing template names from database
            existing = self._repo.list_all(self.workspace_path)
            existing_names = {row["name"] for row in existing}

            # Insert missing builtin templates (all marked as system templates)
            for name, data in DEFAULT_BUILTIN_TEMPLATES.items():
                if name in existing_names:
                    continue  # Skip if already exists

                # Prepare template data
                template_data = {
                    "name": name,
                    "description": data["description"],
                    "tools": data["tools"],
                    "rules": data["rules"],
                    "system_prompt": data["system_prompt"],
                    "model": None,
                    "enabled": True,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
                config_json = json.dumps(template_data, ensure_ascii=False)

                self._repo.create(
                    name=name,
                    config_json=config_json,
                    source='system',
                    workspace_path=self.workspace_path,
                    enabled=True,
                    is_system=True,
                )
                logger.info(f"Initialized system template '{name}'")

        except Exception as e:
            logger.error(f"Failed to init builtin templates: {e}")

    def _load_all_templates(self) -> None:
        """Load all templates from database (both system and user)."""
        try:
            rows = self._repo.list_all(self.workspace_path)

            for row in rows:
                try:
                    config_json = row["config_json"]
                    is_system = bool(row.get("is_system", 0))
                    template = AgentTemplateConfig.from_json(
                        config_json,
                        is_system=is_system,
                        workspace_path=self.workspace_path
                    )
                    self._templates[template.name] = template
                except Exception as e:
                    logger.warning(f"Failed to load template {row.get('name')}: {e}")

            builtin_count = sum(1 for t in self._templates.values() if t.is_builtin)
            user_count = len(self._templates) - builtin_count
            logger.info(f"Loaded {len(self._templates)} templates from database ({builtin_count} builtin, {user_count} user)")
        except Exception as e:
            logger.error(f"Failed to load templates from database: {e}")

    def reload(self) -> bool:
        """Hot reload templates from database."""
        try:
            with self._lock:
                self._templates.clear()
                self._load_all_templates()
            logger.info("Agent templates reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload agent templates: {e}")
            return False

    def reset_system_templates(self) -> dict[str, Any]:
        """
        Reset all system templates to default values.
        This will overwrite any modifications to system templates.

        Returns:
            Dict with reset status and details
        """
        result = {"reset": [], "errors": []}

        try:
            # Delete all existing system templates from database
            existing_system = self._repo.list_system_templates(self.workspace_path)
            for row in existing_system:
                try:
                    self._repo.delete(row["name"], self.workspace_path)
                except Exception as e:
                    result["errors"].append(f"Failed to delete {row['name']}: {e}")

            # Re-create all system templates from defaults
            for name, data in DEFAULT_BUILTIN_TEMPLATES.items():
                try:
                    template_data = {
                        "name": name,
                        "description": data["description"],
                        "tools": data["tools"],
                        "rules": data["rules"],
                        "system_prompt": data["system_prompt"],
                        "model": None,
                        "enabled": True,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }
                    config_json = json.dumps(template_data, ensure_ascii=False)

                    self._repo.create(
                        name=name,
                        config_json=config_json,
                        source="system",
                        workspace_path=self.workspace_path,
                        enabled=True,
                        is_system=True,
                    )
                    result["reset"].append(name)
                except Exception as e:
                    result["errors"].append(f"Failed to reset {name}: {e}")

            # Reload templates
            self._init_and_load()

            logger.info(f"Reset {len(result['reset'])} system templates to defaults")
            return result
        except Exception as e:
            logger.error(f"Failed to reset system templates: {e}")
            result["errors"].append(str(e))
            return result

    def list_templates(self) -> list[AgentTemplateConfig]:
        """Get list of all templates."""
        with self._lock:
            return list(self._templates.values())

    def get_template(self, name: str) -> Optional[AgentTemplateConfig]:
        """Get a template by name."""
        with self._lock:
            return self._templates.get(name)

    def create_template(self, config: AgentTemplateConfig) -> AgentTemplateConfig:
        """Create a new user template."""
        # Validate tools
        invalid_tools = set(config.tools) - VALID_TOOLS
        if invalid_tools:
            raise ValueError(f"Invalid tools: {invalid_tools}")

        config.is_system = False  # User created templates are not system templates
        config.workspace_path = self.workspace_path
        config.created_at = datetime.now().isoformat()
        config.updated_at = config.created_at

        with self._lock:
            self._templates[config.name] = config

        # Save to SQLite
        self._repo.create(
            name=config.name,
            config_json=config.to_json(),
            source="user",
            workspace_path=self.workspace_path,
            enabled=config.enabled,
            is_system=False,
        )

        return config

    def update_template(self, name: str, updates: dict) -> Optional[AgentTemplateConfig]:
        """Update an existing template (system or user)."""
        with self._lock:
            if name not in self._templates:
                return None

            existing = self._templates[name]

            # Apply updates
            if "description" in updates:
                existing.description = updates["description"]
            if "tools" in updates:
                invalid_tools = set(updates["tools"]) - VALID_TOOLS
                if invalid_tools:
                    raise ValueError(f"Invalid tools: {invalid_tools}")
                existing.tools = updates["tools"]
            if "rules" in updates:
                existing.rules = updates["rules"]
            if "system_prompt" in updates:
                existing.system_prompt = updates["system_prompt"]
            if "enabled" in updates:
                existing.enabled = updates["enabled"]
            if "model" in updates:
                existing.model = updates["model"] or None

            existing.updated_at = datetime.now().isoformat()

        # Update in SQLite
        self._repo.update(
            name=name,
            config_json=existing.to_json(),
            workspace_path=self.workspace_path,
            enabled=existing.enabled,
        )

        return self.get_template(name)

    def delete_template(self, name: str) -> bool:
        """Delete a user template."""
        with self._lock:
            if name not in self._templates:
                return False

            if self._templates[name].is_builtin:
                raise ValueError(f"Cannot delete builtin template: {name}")

            del self._templates[name]

        # Delete from SQLite
        self._repo.delete(name, self.workspace_path)
        return True

    def import_from_yaml(self, content: str, on_conflict: str = "skip") -> dict:
        """
        Import templates from YAML content.

        Args:
            content: YAML file content
            on_conflict: How to handle conflicts - "skip", "replace", or "rename"

        Returns:
            Dict with imported, skipped, errors lists
        """
        result: dict[str, Any] = {"imported": [], "skipped": [], "errors": []}

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            result["errors"].append(f"YAML parse error: {e}")
            return result

        if not data or "agents" not in data:
            result["errors"].append("Invalid format: missing 'agents' key")
            return result

        for agent_data in data.get("agents", []):
            try:
                name = agent_data.get("name")
                if not name:
                    result["errors"].append("Template missing 'name' field")
                    continue

                # Validate required fields
                if not all(k in agent_data for k in ["description", "tools", "rules", "system_prompt"]):
                    result["errors"].append(f"{name}: Missing required fields")
                    continue

                # Validate tools
                invalid_tools = set(agent_data["tools"]) - VALID_TOOLS
                if invalid_tools:
                    result["errors"].append(f"{name}: Invalid tools: {invalid_tools}")
                    continue

                # Handle conflicts
                original_name = name
                if name in self._templates:
                    if on_conflict == "skip":
                        result["skipped"].append(name)
                        continue
                    elif on_conflict == "rename":
                        # Generate unique name
                        base_name = name
                        counter = 1
                        while name in self._templates:
                            name = f"{base_name}-{counter}"
                            counter += 1
                        agent_data["name"] = name

                # Create template (imported as user template, not system)
                config = AgentTemplateConfig(
                    name=name,
                    description=agent_data["description"],
                    tools=agent_data["tools"],
                    rules=agent_data["rules"],
                    system_prompt=agent_data["system_prompt"],
                    model=agent_data.get("model"),
                    is_system=False,
                    workspace_path=self.workspace_path,
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                    enabled=True,
                )

                with self._lock:
                    self._templates[name] = config

                # Save to SQLite
                self._repo.upsert(
                    name=name,
                    config_json=config.to_json(),
                    source="user",
                    workspace_path=self.workspace_path,
                    enabled=True,
                    is_system=False,
                )

                result["imported"].append({
                    "name": name,
                    "action": "replaced" if original_name in self._templates else "created"
                })

            except Exception as e:
                result["errors"].append(f"{agent_data.get('name', 'unknown')}: {e}")

        return result

    def export_to_yaml(self, names: Optional[list[str]] = None) -> str:
        """Export templates to YAML format."""
        with self._lock:
            if names:
                templates = [self._templates[n] for n in names if n in self._templates]
            else:
                templates = list(self._templates.values())

        data = {
            "version": "1.0",
            "export_time": datetime.now().isoformat(),
            "agents": [t.to_dict() for t in templates],
        }

        return yaml.dump(data, allow_unicode=True, sort_keys=False)

    def build_system_prompt(self, name: str, task: str, workspace: str) -> Optional[str]:
        """Build the system prompt for a subagent."""
        template = self.get_template(name)
        if not template:
            return None

        rules_text = "\n".join(f"{i+1}. {rule}" for i, rule in enumerate(template.rules))

        return template.system_prompt.format(
            task=task,
            all_rules=rules_text,
            workspace=workspace,
        )

    def get_tools_for_template(self, name: str) -> list[str]:
        """Get list of tools for a template."""
        template = self.get_template(name)
        return template.tools if template else []

    def get_model_for_template(self, name: str) -> Optional[str]:
        """Get the model configured for a template."""
        template = self.get_template(name)
        return template.model if template else None

    def get_all_custom_models(self) -> set[str]:
        """Get all unique custom models configured in templates (for API key pre-registration)."""
        models = set()
        for template in self._templates.values():
            if template.model:
                models.add(template.model)
        return models
