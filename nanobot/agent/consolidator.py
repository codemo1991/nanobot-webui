"""Consolidator: runtime token-budget archiving for session messages."""

from typing import Any

from loguru import logger

from nanobot.session.manager import Session
from nanobot.utils.helpers import estimate_tokens


class Consolidator:
    """
    Archives old session messages into a summary when the unconsolidated
    portion exceeds a token budget.  This prevents long conversations from
    blowing up the context window.
    """

    _SAFETY_BUFFER = 1024
    _MAX_ROUNDS = 3
    _MAX_CHUNK = 40  # max messages to archive per round

    def __init__(
        self,
        provider: Any,
        model: str,
        context_window: int = 65536,
        max_completion: int = 8192,
    ):
        self.provider = provider
        self.model = model
        self.context_window = context_window
        self.max_completion = max_completion

    async def maybe_consolidate(self, session: Session) -> None:
        """Archive old messages if unconsolidated tail exceeds budget."""
        budget = self.context_window - self.max_completion - self._SAFETY_BUFFER
        target = budget // 2

        for _ in range(self._MAX_ROUNDS):
            unconsolidated = session.messages[session.last_consolidated :]
            estimated = sum(self._estimate_msg(m) for m in unconsolidated)
            if estimated <= target:
                break

            boundary = self._pick_boundary(unconsolidated, estimated - target)
            if boundary <= 1:
                break

            chunk = unconsolidated[:boundary]
            summary = await self._summarize(chunk)

            # Insert summary as a system message at the consolidation boundary
            session.messages.insert(
                session.last_consolidated,
                {
                    "role": "system",
                    "content": f"[Earlier conversation summarized]\n{summary}",
                    "timestamp": chunk[-1].get("timestamp", ""),
                },
            )
            session.last_consolidated += 1  # the summary message itself
            session.last_consolidated += boundary  # archived original messages
            logger.info(
                f"[Consolidator] Archived {boundary} messages for {session.key}, "
                f"remaining unconsolidated ~{sum(self._estimate_msg(m) for m in session.messages[session.last_consolidated:])} tokens"
            )

    def _pick_boundary(
        self, messages: list[dict[str, Any]], tokens_to_remove: int
    ) -> int:
        """Pick a chunk boundary aligned to a user turn."""
        removed = 0
        last_boundary = 1
        for i, m in enumerate(messages):
            if i > 0 and m.get("role") == "user":
                last_boundary = i
                if removed >= tokens_to_remove:
                    return last_boundary
            removed += self._estimate_msg(m)
        return min(last_boundary, len(messages))

    async def _summarize(self, messages: list[dict[str, Any]]) -> str:
        """Ask the LLM to summarize a chunk of messages."""
        formatted = "\n".join(
            f"{m.get('role')}: {str(m.get('content', ''))[:500]}" for m in messages
        )
        try:
            resp = await self.provider.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the following conversation into 3-5 bullet points "
                            "of key facts, decisions, or user preferences. "
                            "Output ONLY the bullet points."
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            return (resp.content or "[no summary]").strip()
        except Exception as e:
            logger.warning(f"[Consolidator] Summary failed: {e}")
            return "[conversation archived without summary]"

    @staticmethod
    def _estimate_msg(m: dict[str, Any]) -> int:
        """Rough token estimate for a message."""
        return estimate_tokens(m.get("content", "")) + 4
