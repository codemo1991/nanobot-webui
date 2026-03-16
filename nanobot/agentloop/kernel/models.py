"""AgentLoop 核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSpec:
    """任务规格，用于 spawn 子任务。"""

    task_kind: str
    capability_name: str
    intent: str
    priority: int = 100
    budget_tokens: int = 0
    budget_millis: int = 0
    budget_cost_cents: int = 0
    deadline_ts: int | None = None
    max_retries: int = 1
    input_schema: str | None = None
    output_schema: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRef:
    """Artifact 引用。"""

    artifact_id: str
    artifact_type: str


@dataclass
class CapabilityResult:
    """Capability 执行结果。"""

    status: str  # DONE / WAITING_CHILDREN / WAITING_ARTIFACTS / FAILED
    output_artifact: dict[str, Any] | None = None
    spawn_specs: list[TaskSpec] = field(default_factory=list)
    read_artifacts: list[str] = field(default_factory=list)
    write_artifacts: list[dict[str, Any]] = field(default_factory=list)
    wait_for_artifacts: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
