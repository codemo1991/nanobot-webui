"""ID 生成与时间戳工具。"""

import time
import uuid


def now_ts() -> int:
    """当前 Unix 时间戳（秒）。"""
    return int(time.time())


def new_id(prefix: str) -> str:
    """生成带前缀的唯一 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
