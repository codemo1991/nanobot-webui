"""File watcher for Claude Code results."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus


class ResultWatcher(FileSystemEventHandler):
    """
    Watches for Claude Code result files and notifies via MessageBus.
    
    This replaces polling with event-driven notification,
    eliminating extra token consumption.
    """
    
    def __init__(
        self,
        result_dir: Path,
        bus: "MessageBus | None" = None,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.result_dir = Path(result_dir)
        self.bus = bus
        self.on_result = on_result
        self._observer = Observer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
    
    def start(self) -> None:
        """Start watching the result directory."""
        if self._started:
            return
        
        self.result_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        
        self._observer.schedule(self, str(self.result_dir), recursive=False)
        self._observer.start()
        self._started = True
        logger.debug(f"ResultWatcher started on {self.result_dir}")
    
    def stop(self) -> None:
        """Stop watching."""
        if self._started:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._started = False
            logger.debug("ResultWatcher stopped")
    
    def on_created(self, event: FileCreatedEvent) -> None:
        """Handle new result file."""
        if event.is_directory:
            return
        
        path = Path(event.src_path)
        
        if not path.name.endswith('.json'):
            return
        
        if path.name.endswith('.meta.json') or path.name.endswith('.hook.json'):
            return
        
        if path.name.startswith('.'):
            return
        
        logger.debug(f"ResultWatcher detected new file: {path.name}")
        
        try:
            result = self._read_result(path)
            if result:
                self._handle_result(result)
        except Exception as e:
            logger.warning(f"Failed to process result file {path}: {e}")
    
    def _read_result(self, path: Path) -> dict[str, Any] | None:
        """Read and parse a result file."""
        try:
            content = path.read_text(encoding="utf-8")
            result = json.loads(content)
            
            if "task_id" not in result:
                logger.warning(f"Result file missing task_id: {path}")
                return None
            
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in result file {path}: {e}")
            return None
        except IOError as e:
            logger.warning(f"Failed to read result file {path}: {e}")
            return None
    
    def _handle_result(self, result: dict[str, Any]) -> None:
        """Handle a parsed result."""
        task_id = result.get("task_id", "unknown")
        status = result.get("status", "unknown")
        origin = result.get("origin", {})
        
        logger.info(f"Claude Code task [{task_id}] completed with status: {status}")
        
        if self.on_result:
            try:
                self.on_result(result)
            except Exception as e:
                logger.warning(f"on_result callback failed: {e}")
        
        if self.bus and origin:
            self._notify_via_bus(result, origin)
    
    def _notify_via_bus(self, result: dict[str, Any], origin: dict[str, str]) -> None:
        """Send result notification via MessageBus."""
        from nanobot.bus.events import InboundMessage
        
        task_id = result.get("task_id", "unknown")
        status = result.get("status", "done")
        output = result.get("output", "")
        
        status_text = {
            "done": "completed successfully",
            "timeout": "timed out",
            "error": "failed with error",
        }.get(status, f"finished with status: {status}")
        
        content = f"""[Claude Code task '{task_id}' {status_text}]

Result:
{output[:2000]}{"..." if len(output) > 2000 else ""}

Summarize this for the user naturally. Keep it brief."""
        
        msg = InboundMessage(
            channel="system",
            sender_id="claude-code",
            chat_id=f"{origin.get('channel', 'cli')}:{origin.get('chat_id', 'direct')}",
            content=content,
        )
        
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._publish_message(msg))
            )
        else:
            asyncio.run(self._publish_message(msg))
    
    async def _publish_message(self, msg: "InboundMessage") -> None:
        """Publish message to bus (async wrapper)."""
        if self.bus:
            await self.bus.publish_inbound(msg)
