"""
Cron-friendly entry point for trace analysis.

Can be run as a standalone script::

    python -m nanobot.tracing.scheduler

Or imported::

    from nanobot.tracing.scheduler import run_analysis
    rc = run_analysis(date_from="2026-03-20", date_to="2026-03-27", memory=True)

Exit codes:
    0 — success
    1 — error
"""

from __future__ import annotations

import sys
from datetime import date, timedelta


def run_analysis(
    date_from: str | None = None,
    date_to: str | None = None,
    memory: bool = True,
    error_rate: float = 0.1,
    latency_ms: float = 5000.0,
    success_rate: float = 0.8,
    output_json: bool = False,
) -> int:
    """
    Run the trace analysis pipeline.

    Args:
        date_from: Start date YYYY-MM-DD (default: 7 days ago).
        date_to: End date YYYY-MM-DD (default: today).
        memory: Whether to persist to memory (default: True).
        error_rate: Error rate threshold (default: 0.1).
        latency_ms: Latency p95 threshold in ms (default: 5000.0).
        success_rate: Success rate threshold (default: 0.8).
        output_json: Output report as JSON instead of text (default: False).

    Returns:
        0 on success, 1 on error.
    """
    # Apply date defaults
    today = date.today().isoformat()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = (date.today() - timedelta(days=7)).isoformat()

    try:
        from nanobot.tracing.anomaly import AnomalyConfig
        from nanobot.tracing.service import TraceAnalysisService

        config = AnomalyConfig(
            error_rate_threshold=error_rate,
            latency_p95_threshold_ms=latency_ms,
            success_rate_threshold=success_rate,
        )

        service = TraceAnalysisService(
            date_from=date_from,
            date_to=date_to,
            anomaly_config=config,
        )

        if memory:
            report, result = service.run_with_memory()
            if output_json:
                print(report.to_json())
            else:
                print(str(report))
            if result.success:
                print("Memory written.", file=sys.stdout)
            else:
                # Still successful overall, but memory had issues
                print(f"Memory partial failure: {result.error}", file=sys.stdout)
        else:
            report = service.run()
            if output_json:
                print(report.to_json())
            else:
                print(str(report))

        return 0

    except Exception as exc:  # pragma: no cover — defensive wrapper
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Minimal argument parsing for direct invocation:
    #   python -m nanobot.tracing.scheduler [--json] [--no-memory] [--date-from YYYY-MM-DD] ...
    import argparse

    parser = argparse.ArgumentParser(description="Run trace analysis (cron-friendly).")
    parser.add_argument("--date-from", dest="date_from", default=None)
    parser.add_argument("--date-to", dest="date_to", default=None)
    parser.add_argument(
        "--no-memory",
        dest="memory",
        action="store_false",
        default=True,
        help="Disable memory persistence (default: enabled)",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        default=False,
        help="Output report as JSON",
    )
    parser.add_argument("--error-rate", type=float, default=0.1)
    parser.add_argument("--latency-ms", type=float, default=5000.0)
    parser.add_argument("--success-rate", type=float, default=0.8)
    args = parser.parse_args()

    rc = run_analysis(
        date_from=args.date_from,
        date_to=args.date_to,
        memory=args.memory,
        error_rate=args.error_rate,
        latency_ms=args.latency_ms,
        success_rate=args.success_rate,
        output_json=args.output_json,
    )
    raise SystemExit(rc)
