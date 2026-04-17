"""Base class for agent tools."""

import uuid
from abc import ABC, abstractmethod
from typing import Any

from nanobot.agent.tools.progress import ToolProgressCallback, ToolProgressThrottler


class Tool(ABC):
    """
    Abstract base class for agent tools.
    
    Tools are capabilities that the agent can use to interact with
    the environment, such as reading files, executing commands, etc.
    """
    
    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    def __init__(
        self,
        progress_callback: ToolProgressCallback | None = None,
        tool_id: str | None = None,
    ) -> None:
        """
        Initialize the tool.

        Args:
            progress_callback: Optional callback for progress updates.
            tool_id: Optional unique identifier for this tool instance.
                    If not provided, a UUID will be generated.
        """
        self._progress_callback = progress_callback
        self._tool_id = tool_id or str(uuid.uuid4())
        self._progress_throttler = ToolProgressThrottler(min_interval=1.0)

    @property
    def tool_id(self) -> str:
        """Unique identifier for this tool instance."""
        return self._tool_id

    def report_progress(self, detail: str, percent: int | None = None) -> None:
        """
        Report progress update for this tool.

        Args:
            detail: Human-readable progress message.
            percent: Optional progress percentage (0-100).
        """
        if self._progress_callback is None:
            return

        if not self._progress_throttler.should_push(self._tool_id):
            return

        # Convert percent (0-100) to progress (0.0-1.0)
        progress = percent / 100.0 if percent is not None else None
        self._progress_callback(self._tool_id, detail, progress)

    def report_stream_chunk(self, chunk: str, is_error: bool = False) -> None:
        """
        Report a stream chunk from the tool execution.

        This is a placeholder method for future streaming support.

        Args:
            chunk: The output chunk.
            is_error: Whether this chunk represents error output.
        """
        # Placeholder for future streaming implementation
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    @property
    def server_id(self) -> str | None:
        """MCP server ID for MCP tools, None for built-in tools."""
        return None

    @property
    def deferred(self) -> bool:
        """If True, tool schema is not injected in initial LLM context (loaded on-demand)."""
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        """Whether the tool can be safely executed concurrently with other tools."""
        return True

    @property
    def is_read_only(self) -> bool:
        """Whether the tool only reads data and doesn't modify state (safe for retries)."""
        return False

    @property
    def is_destructive(self) -> bool:
        """Whether the tool performs destructive operations."""
        return False

    @property
    def danger_level(self) -> int:
        """Risk level 0-10: 0=safe, 10=extremely dangerous (e.g., delete, execute)."""
        return 0

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the tool with given parameters.
        
        Args:
            **kwargs: Tool-specific parameters.
        
        Returns:
            String result of the tool execution.
        """
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """Validate tool parameters against JSON schema. Returns error list (empty if valid)."""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        t, label = schema.get("type"), path or "parameter"
        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]
        
        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + '.' + k if path else k))
        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                errors.extend(self._validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]"))
        return errors
    
    def to_schema(self) -> dict[str, Any]:
        """Convert tool to OpenAI function schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
