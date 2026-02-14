#!/usr/bin/env python3
"""Claude Code Stop Hook for nanobot integration.

This script is called by Claude Code when a task completes.
It writes the result to a JSON file for nanobot to pick up.

Environment variables set by nanobot:
- NANOBOT_TASK_ID: Unique task identifier
- NANOBOT_TASK_META: Path to task metadata JSON file
- NANOBOT_RESULT_DIR: Directory to write result files

Environment variables set by Claude Code:
- CLAUDE_OUTPUT: Task output (if available)
- CLAUDE_CODE_OUTPUT: Alternative output variable
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main():
    task_meta_path = os.environ.get("NANOBOT_TASK_META", "")
    if not task_meta_path:
        sys.exit(0)
    
    task_meta_path = Path(task_meta_path)
    if not task_meta_path.exists():
        sys.exit(0)
    
    try:
        with open(task_meta_path, encoding="utf-8") as f:
            task_meta = json.load(f)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)
    
    task_id = task_meta.get("task_id", "unknown")
    result_dir = Path(task_meta.get("result_dir", "."))
    origin = task_meta.get("origin", {})
    
    output = ""
    for env_var in ["CLAUDE_OUTPUT", "CLAUDE_CODE_OUTPUT", "CLAUDE_SESSION_OUTPUT"]:
        if env_var in os.environ:
            output = os.environ[env_var]
            break
    
    lock_file = result_dir / f".{task_id}.lock"
    if lock_file.exists():
        sys.exit(0)
    
    try:
        lock_file.touch()
    except IOError:
        sys.exit(0)
    
    try:
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": output,
            "status": "done",
            "origin": origin,
        }
        
        result_path = result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        
        try:
            task_meta_path.unlink()
        except IOError:
            pass
        
        hook_file = result_dir / f"{task_id}.hook.json"
        try:
            hook_file.unlink()
        except IOError:
            pass
            
    finally:
        try:
            lock_file.unlink()
        except IOError:
            pass


if __name__ == "__main__":
    main()
