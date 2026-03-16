"""Artifact 仓储操作。"""

import hashlib
import json
from pathlib import Path

from nanobot.agentloop.db import tx
from nanobot.agentloop.kernel.ids import new_id, now_ts

# 大于此大小的 payload 落盘存储
PAYLOAD_INLINE_THRESHOLD = 64 * 1024  # 64KB
ARTIFACT_DATA_DIR = Path("data/artifacts")


def stable_hash(text: str) -> str:
    """计算文本的稳定哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_artifact(
    conn,
    trace_id: str,
    producer_task_id: str,
    artifact_type: str,
    payload: dict,
    workspace_root: Path | None = None,
) -> str:
    """创建 artifact，返回 artifact_id。>=64KB 时落盘存储。"""
    if not isinstance(payload, dict):
        payload = {}
    ts = now_ts()
    payload_text = json.dumps(payload, ensure_ascii=False)
    payload_bytes = payload_text.encode("utf-8")
    artifact_hash = stable_hash(payload_text)
    artifact_id = new_id("af")

    if len(payload_bytes) >= PAYLOAD_INLINE_THRESHOLD and workspace_root is not None:
        root = Path(workspace_root).expanduser().resolve()
        trace_dir = root / ARTIFACT_DATA_DIR / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        payload_path = trace_dir / f"{artifact_id}.json"
        payload_path.write_text(payload_text, encoding="utf-8")
        storage_kind = "FILE"
        payload_text_val = None
        payload_path_val = str(payload_path)
    else:
        storage_kind = "INLINE"
        payload_text_val = payload_text
        payload_path_val = None

    with tx(conn, immediate=True):
        conn.execute(
            """
            INSERT INTO agentloop_artifacts(
                artifact_id, trace_id, producer_task_id, artifact_type, version,
                status, storage_kind, payload_text, payload_path, payload_hash,
                confidence, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, 'READY', ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                artifact_id,
                trace_id,
                producer_task_id,
                artifact_type,
                storage_kind,
                payload_text_val,
                payload_path_val,
                artifact_hash,
                json.dumps({}, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, ?, 'ARTIFACT_READY', ?, ?)
            """,
            (
                trace_id,
                producer_task_id,
                None,
                json.dumps({"artifact_id": artifact_id, "artifact_type": artifact_type}),
                ts,
            ),
        )

    return artifact_id


def get_artifact_payload(conn, artifact_id: str) -> dict | None:
    """读取 artifact 的 payload（支持 INLINE 与 FILE）。"""
    row = conn.execute(
        "SELECT storage_kind, payload_text, payload_path FROM agentloop_artifacts WHERE artifact_id = ? AND status = 'READY'",
        (artifact_id,),
    ).fetchone()
    if not row:
        return None
    try:
        if row["storage_kind"] == "FILE" and row["payload_path"]:
            path = Path(row["payload_path"])
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            return None
        if row["payload_text"]:
            return json.loads(row["payload_text"])
    except json.JSONDecodeError:
        return None
    return None
