"""Agent template management for configurable subagents."""

import json
import threading
import yaml
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from nanobot.storage.agent_template_repository import AgentTemplateRepository


@dataclass
class AgentTemplateConfig:
    """Configuration for a subagent template."""

    name: str
    description: str
    tools: list[str]
    rules: list[str]
    system_prompt: str
    source: str = "builtin"  # "builtin" | "user_yaml"
    model: Optional[str] = None  # Optional: use specific model for this template
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    enabled: bool = True
    workspace_path: str = ""  # For multi-workspace isolation

    @property
    def is_builtin(self) -> bool:
        """Check if this is a system built-in template."""
        return self.source == "builtin"

    @property
    def is_editable(self) -> bool:
        """Check if this template can be edited."""
        return not self.is_builtin

    @property
    def is_deletable(self) -> bool:
        """Check if this template can be deleted."""
        return not self.is_builtin

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
    def from_json(cls, json_str: str, source: str = "user_yaml", workspace_path: str = "") -> "AgentTemplateConfig":
        """Create from JSON string."""
        data = json.loads(json_str)
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            tools=data.get("tools", []),
            rules=data.get("rules", []),
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model"),
            source=source,
            enabled=data.get("enabled", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            workspace_path=workspace_path,
        )


# Valid tools that can be assigned to subagents
VALID_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "exec",
    "web_search",
    "web_fetch",
    "claude_code",  # Claude Code CLI tool
}

# Builtin templates that are user-editable (stored as user_yaml)
USER_EDITABLE_TEMPLATES = {"claude-coder", "vision"}


# Builtin template definitions (loaded from YAML file)
DEFAULT_BUILTIN_TEMPLATES = {
    "minimal": {
        "description": "快速简单任务",
        "tools": ["read_file", "write_file", "list_dir", "exec", "web_search", "web_fetch"],
        "rules": [
            "Stay focused - complete only the assigned task",
            "Be concise in your response",
            "Complete the task thoroughly",
        ],
        "system_prompt": """# Subagent

You are a subagent spawned by the main agent to complete a specific task.

## Your Task
{task}

## Rules
{all_rules}

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents
- Access the main agent's conversation history

## Workspace
Your workspace is at: {workspace}

When you have completed the task, provide a clear summary of your findings or actions.""",
    },
    "coder": {
        "description": "代码编写任务",
        "tools": ["read_file", "write_file", "edit_file", "list_dir", "exec"],
        "rules": [
            "Follow the project's existing code conventions and style",
            "Write clean, readable, and well-documented code",
            "Include appropriate error handling",
            "Write tests when appropriate",
            "Consider performance and security",
            "Keep functions focused and single-purpose",
        ],
        "system_prompt": """# Coder Subagent

You are a professional software developer subagent.

## Your Task
{task}

## Rules
{all_rules}

## Capabilities
- Read existing code files to understand project structure
- Write new code files and edit existing ones
- Execute shell commands (for running tests, linting, building, etc.)
- Search for code patterns, symbols, and dependencies

## Code Quality Standards
- Follow the existing code style in the project
- Use meaningful variable and function names
- Add comments for complex logic
- Handle errors gracefully
- Write modular, reusable code

## Workspace
Your workspace is at: {workspace}

When complete, describe what was done, what files were changed, and any important notes.""",
    },
    "researcher": {
        "description": "信息检索研究",
        "tools": ["web_search", "web_fetch", "read_file"],
        "rules": [
            "Provide accurate and verified information",
            "Always cite your sources",
            "Distinguish between facts and opinions",
            "Avoid speculation without evidence",
            "Be thorough in your research",
        ],
        "system_prompt": """# Researcher Subagent

You are a research assistant subagent.

## Your Task
{task}

## Rules
{all_rules}

## What You Can Do
- Search the web for relevant information
- Fetch and analyze web pages
- Read local files for context
- Synthesize information from multiple sources

## Research Standards
1. Verify information from multiple sources when possible
2. Clearly distinguish between facts and opinions
3. Provide source citations for key findings
4. Be objective and unbiased
5. Acknowledge limitations or uncertainties

## Output Format
- Start with a brief executive summary
- Present findings in a structured way
- Include relevant links or references
- End with conclusions and next steps

When complete, provide a well-organized summary of your research findings.""",
    },
    "analyst": {
        "description": "数据分析任务",
        "tools": ["read_file", "write_file", "exec", "web_search", "web_fetch"],
        "rules": [
            "Base conclusions on data and evidence",
            "Provide clear, actionable insights",
            "Use appropriate analytical methods",
            "Present data in readable formats",
            "Acknowledge data limitations",
        ],
        "system_prompt": """# Analyst Subagent

You are a data analyst subagent.

## Your Task
{task}

## Rules
{all_rules}

## What You Can Do
- Read and parse data files
- Execute commands for data processing
- Search for relevant context online
- Write analysis reports

## Analysis Standards
1. Start by understanding the data available
2. Apply appropriate analytical methods
3. Look for patterns, trends, and anomalies
4. Support conclusions with evidence
5. Suggest practical next steps

## Output Format
- Executive summary (key findings)
- Methodology (how you analyzed)
- Detailed findings
- Conclusions and recommendations
- Any caveats or limitations

When complete, provide a comprehensive analysis with clear conclusions.""",
    },
    "claude-coder": {
        "description": "Claude Code 代码编写任务（使用 Claude Code CLI 后端）",
        "tools": ["read_file", "write_file", "edit_file", "list_dir", "exec"],
        "rules": [
            "Use Claude Code CLI for all code operations",
            "Follow the project's existing code conventions and style",
            "Write clean, readable, and well-documented code",
            "Include appropriate error handling",
            "Write tests when appropriate",
            "Consider performance and security",
            "Keep functions focused and single-purpose",
            "Take advantage of Claude Code's capabilities for intelligent code assistance",
        ],
        "system_prompt": """# Claude Code Coder Subagent

You are a professional software developer subagent powered by Claude Code CLI.

## Your Task
{task}

## Rules
{all_rules}

## Capabilities (via Claude Code CLI)
- Read existing code files to understand project structure
- Write new code files and edit existing ones
- Execute shell commands (for running tests, linting, building, etc.)
- Search for code patterns, symbols, and dependencies
- Get intelligent code suggestions from Claude Code
- Automatically review and improve code

## Code Quality Standards
- Follow the existing code style in the project
- Use meaningful variable and function names
- Add comments for complex logic
- Handle errors gracefully
- Write modular, reusable code

## Workspace
Your workspace is at: {workspace}

## Approach
1. Let Claude Code analyze the project structure
2. Implement the solution with Claude Code's assistance
3. Run tests or linters if available to validate correctness
4. Provide a concise summary of all files created or modified

When complete, describe what was done, what files were changed, and any important notes for the user.""",
    },
    "vision": {
        "description": "图片识别与分析（需要视觉模型支持）",
        "tools": ["read_file", "web_fetch"],
        "rules": [
            "Analyze images thoroughly and describe all visual elements",
            "Extract text from images (OCR) when present",
            "Identify objects, people, scenes, and activities",
            "Note colors, layouts, styles, and designs",
            "Provide detailed and accurate descriptions",
            "If image is unclear or unrecognizable, state that clearly",
        ],
        "system_prompt": """# Vision Subagent

You are a vision-enabled subagent specialized in analyzing and describing images.

## Your Task
Analyze the provided image(s) and provide a detailed description.

## Rules
{all_rules}

## What You Can Do
- Analyze images and describe visual content in detail
- Extract text from images (OCR)
- Identify objects, people, scenes, and activities
- Note colors, layouts, styles, and designs
- Provide accurate and detailed descriptions

## Output Format
- Start with a brief summary of what the image shows
- Provide detailed analysis of:
  - Main subjects/objects
  - Background/environment
  - Text content (if any)
  - Colors and visual style
  - Any notable details
- End with any relevant conclusions or observations

When complete, provide a comprehensive description of the image.""",
    },
}


class AgentTemplateManager:
    """
    Manages agent templates with support for:
    - Built-in default templates (read-only)
    - User-defined templates (stored in SQLite)
    - YAML import/export
    - Hot reload
    """

    def __init__(self, workspace: Path):
        from nanobot.storage import memory_repository

        self.workspace = workspace
        self.workspace_path = str(workspace)

        # 使用与系统相同的数据库路径逻辑：
        # 优先使用 workspace 数据库，否则使用默认数据库
        workspace_db_path = memory_repository.get_workspace_db_path(workspace)
        if workspace_db_path.exists():
            db_path = workspace_db_path
        else:
            db_path = memory_repository.get_default_db_path()

        # In-memory cache
        self._templates: dict[str, AgentTemplateConfig] = {}
        self._lock = threading.RLock()

        # Repository for user templates (SQLite)
        self._repo = AgentTemplateRepository(db_path)

        # Load all templates
        self._load()

    def _load(self) -> None:
        """Load templates from builtin and database."""
        with self._lock:
            self._templates.clear()

            # 1. Load builtin templates
            self._load_builtin_templates()

            # 2. Load user templates from SQLite (override builtin with same name)
            self._load_user_templates()

    def _load_builtin_templates(self) -> None:
        """Load builtin templates."""
        for name, data in DEFAULT_BUILTIN_TEMPLATES.items():
            # 确定模板来源：用户可编辑的模板设为 user_yaml
            source = "user_yaml" if name in USER_EDITABLE_TEMPLATES else "builtin"

            template = AgentTemplateConfig(
                name=name,
                description=data["description"],
                tools=data["tools"],
                rules=data["rules"],
                system_prompt=data["system_prompt"],
                source=source,
                enabled=True,
                workspace_path=self.workspace_path,
            )
            self._templates[name] = template
        logger.info(f"Loaded {len(DEFAULT_BUILTIN_TEMPLATES)} builtin templates ({len(USER_EDITABLE_TEMPLATES)} user-editable)")

    def _load_user_templates(self) -> None:
        """Load user templates from SQLite."""
        try:
            rows = self._repo.list_all(self.workspace_path)
            db_template_names = {row["name"] for row in rows}

            # 首次加载：将用户可编辑的内置模板保存到数据库
            for name in USER_EDITABLE_TEMPLATES:
                if name in self._templates and name not in db_template_names:
                    template = self._templates[name]
                    self._repo.upsert(
                        name=template.name,
                        config_json=template.to_json(),
                        source="user_yaml",
                        workspace_path=self.workspace_path,
                        enabled=template.enabled,
                    )
                    logger.info(f"Saved user-editable template '{name}' to database")

            # 重新加载以获取数据库中的模板
            rows = self._repo.list_all(self.workspace_path)

            for row in rows:
                try:
                    config_json = row["config_json"]
                    template = AgentTemplateConfig.from_json(
                        config_json,
                        source="user_yaml",
                        workspace_path=self.workspace_path
                    )
                    # Override builtin with user template if same name
                    self._templates[template.name] = template
                except Exception as e:
                    logger.warning(f"Failed to load template {row.get('name')}: {e}")
            logger.info(f"Loaded {len(rows)} user templates from database")
        except Exception as e:
            logger.error(f"Failed to load user templates: {e}")

    def reload(self) -> bool:
        """Hot reload templates."""
        try:
            self._load()
            logger.info("Agent templates reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload agent templates: {e}")
            return False

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
        if config.name in self._templates and self._templates[config.name].is_builtin:
            raise ValueError(f"Cannot override builtin template: {config.name}")

        # Validate tools
        invalid_tools = set(config.tools) - VALID_TOOLS
        if invalid_tools:
            raise ValueError(f"Invalid tools: {invalid_tools}")

        config.source = "user_yaml"
        config.workspace_path = self.workspace_path
        config.created_at = datetime.now().isoformat()
        config.updated_at = config.created_at

        with self._lock:
            self._templates[config.name] = config

        # Save to SQLite
        self._repo.create(
            name=config.name,
            config_json=config.to_json(),
            source="user_yaml",
            workspace_path=self.workspace_path,
            enabled=config.enabled,
        )

        return config

    def update_template(self, name: str, updates: dict) -> Optional[AgentTemplateConfig]:
        """Update an existing user template."""
        with self._lock:
            if name not in self._templates:
                return None

            existing = self._templates[name]
            if existing.is_builtin:
                raise ValueError(f"Cannot modify builtin template: {name}")

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
        result = {"imported": [], "skipped": [], "errors": []}

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
                    existing = self._templates[name]
                    if existing.is_builtin:
                        # Cannot override builtin, skip
                        result["skipped"].append(name)
                        continue

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

                # Create template
                config = AgentTemplateConfig(
                    name=name,
                    description=agent_data["description"],
                    tools=agent_data["tools"],
                    rules=agent_data["rules"],
                    system_prompt=agent_data["system_prompt"],
                    model=agent_data.get("model"),
                    source="user_yaml",
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
                    source="user_yaml",
                    workspace_path=self.workspace_path,
                    enabled=True,
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
        """Get all unique custom models configured in user templates."""
        models = set()
        for template in self._templates.values():
            if template.model and template.source == "user_yaml":
                models.add(template.model)
        return models
