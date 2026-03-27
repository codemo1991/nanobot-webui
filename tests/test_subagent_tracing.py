"""Tests for subagent tracing integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_run_subagent_accepts_parent_span_id():
    """Test that _run_subagent accepts parent_span_id parameter."""
    from nanobot.agent.subagent import SubagentManager
    import inspect
    sig = inspect.signature(SubagentManager._run_subagent)
    assert "parent_span_id" in sig.parameters


def test_run_subagent_impl_accepts_subagent_span():
    """Test that _run_subagent_impl accepts subagent_span parameter."""
    from nanobot.agent.subagent import SubagentManager
    import inspect
    sig = inspect.signature(SubagentManager._run_subagent_impl)
    assert "subagent_span" in sig.parameters


@pytest.mark.asyncio
async def test_subagent_span_created_with_correct_attrs():
    """Test that _run_subagent creates span with correct parent and attrs.

    Verifies via the subagent_span argument passed to _run_subagent_impl:
    - span has correct parent_id
    - span has correct attrs (subagent_id, template)
    - mark_subagent_span was called (sets span_type=subagent, subagent_id, subagent_intent)
    - impl receives the same span object
    """
    from nanobot.agent.subagent import SubagentManager
    from nanobot.tracing.spans import Span

    with patch.object(SubagentManager, '__init__', lambda self, **kw: None):
        manager = SubagentManager(
            provider=MagicMock(),
            workspace=MagicMock(),
            bus=MagicMock(),
        )
        manager.model = "test-model"
        manager._main_model = "test-model"
        manager.provider = MagicMock()
        manager.brave_api_key = None
        manager.exec_config = None
        manager.sessions = None
        manager._running_tasks = {}
        manager._max_concurrent_subagents = 10
        manager._subagent_semaphore = AsyncMock()
        manager._session_cancelled = set()
        manager._batch_tasks = {}
        manager._batch_lock = MagicMock()
        manager._tools_cache = {}
        manager._claude_code_manager = None
        manager._claude_code_permission_mode = "auto"
        manager._status_service = None
        manager._agent_template_manager = None
        manager._chain_monitor = MagicMock()
        manager._chain_monitor.get_current_chain.return_value = None
        manager._backend_registry = MagicMock()
        manager._backend_registry.get.return_value = None
        manager._backend_resolver = MagicMock()
        manager._backend_resolver.resolve.return_value = "native"
        manager._is_last_in_batch = MagicMock(return_value=True)

        captured = {}

        # Mock _run_subagent_impl to capture what it received.
        # When a plain function replaces an instance method, Python passes
        # args exactly as specified (no auto-binding of self):
        #   mock_impl(task_id, task, label, origin, *, template, ..., subagent_span)
        async def mock_impl_caller(*args, **kwargs):
            captured["subagent_span"] = kwargs.get("subagent_span")
            captured["task_id"] = args[0] if len(args) > 0 else None
            captured["template"] = kwargs.get("template")

        manager._run_subagent_impl = mock_impl_caller

        # Capture the real Span instances created during the call
        span_instances = []
        original_init = Span.__init__

        def patched_init(self, *args, **kwargs):
            span_instances.append(self)
            return original_init(self, *args, **kwargs)

        with patch.object(Span, '__init__', patched_init):
            await manager._run_subagent(
                task_id="sub_123",
                task="Do something",
                label="My Task",
                origin={"channel": "cli", "chat_id": "c1"},
                template="coder",
                parent_span_id="parent-abc",
                backend="native",
            )

        # Exactly one span should have been created (the subagent.spawn span)
        assert len(span_instances) == 1, f"Expected 1 span, got {len(span_instances)}"
        real_span_instance = span_instances[0]

        # Verify span was created with correct parent and attrs
        assert real_span_instance.parent_id == "parent-abc"
        assert real_span_instance.attrs["subagent_id"] == "sub_123"
        assert real_span_instance.attrs["template"] == "coder"
        assert real_span_instance.attrs["backend"] == "native"

        # Verify mark_subagent_span was called (sets span_type, subagent_id, subagent_intent)
        assert real_span_instance.span_type == "subagent"
        assert real_span_instance.subagent_id == "sub_123"
        assert real_span_instance.subagent_intent == "My Task"

        # Verify impl received the same span object
        assert captured["subagent_span"] is real_span_instance
        assert captured["task_id"] == "sub_123"
        assert captured["template"] == "coder"


@pytest.mark.asyncio
async def test_subagent_span_error_end():
    """Test that span is ended with error status when impl raises.

    When _run_subagent_impl raises before entering its try block (e.g., at the
    start), the exception propagates to the outer span's __aexit__ which marks
    it with error_type/error_msg and ends it with status="error".
    """
    from nanobot.agent.subagent import SubagentManager
    from nanobot.tracing.spans import Span

    with patch.object(SubagentManager, '__init__', lambda self, **kw: None):
        manager = SubagentManager(
            provider=MagicMock(),
            workspace=MagicMock(),
            bus=MagicMock(),
        )
        manager.model = "test-model"
        manager._main_model = "test-model"
        manager.provider = MagicMock()
        manager.brave_api_key = None
        manager.exec_config = None
        manager.sessions = None
        manager._running_tasks = {}
        manager._max_concurrent_subagents = 10
        manager._subagent_semaphore = AsyncMock()
        manager._session_cancelled = set()
        manager._batch_tasks = {}
        manager._batch_lock = MagicMock()
        manager._tools_cache = {}
        manager._claude_code_manager = None
        manager._claude_code_permission_mode = "auto"
        manager._status_service = None
        manager._agent_template_manager = None
        manager._chain_monitor = MagicMock()
        manager._chain_monitor.get_current_chain.return_value = None
        manager._backend_registry = MagicMock()
        manager._backend_registry.get.return_value = None
        manager._backend_resolver = MagicMock()
        manager._backend_resolver.resolve.return_value = "native"
        manager._is_last_in_batch = MagicMock(return_value=True)

        # Mock impl raises immediately (before entering _run_subagent_impl's try block).
        # args[0] is manager instance (self_ref) when called as instance attribute.
        async def raising_impl(self_ref, *args, **kwargs):
            raise RuntimeError("Boom!")

        manager._run_subagent_impl = raising_impl

        # Capture the real Span instance
        span_instances = []
        original_init = Span.__init__

        def patched_init(self, *args, **kwargs):
            span_instances.append(self)
            return original_init(self, *args, **kwargs)

        with patch.object(Span, '__init__', patched_init):
            with pytest.raises(RuntimeError, match="Boom!"):
                await manager._run_subagent(
                    task_id="err_123",
                    task="Failing task",
                    label="Error Task",
                    origin={"channel": "cli", "chat_id": "c1"},
                    template="minimal",
                    parent_span_id="parent-xyz",
                    backend="native",
                )

        # Exactly one span was created and it was ended with error status
        assert len(span_instances) == 1
        span = span_instances[0]
        assert span.status == "error"
        assert span.end_ms is not None  # span was ended
        # Outer span's __aexit__ sets error_type and error_msg
        assert span.attrs.get("error_type") == "RuntimeError"
        assert span.attrs.get("error_msg") == "Boom!"
