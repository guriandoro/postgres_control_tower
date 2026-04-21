"""Alerting subsystem (P7).

Public entry points:

- :func:`evaluate_rules` — run all rules once and persist alert state.
- :func:`refresh_storage_forecasts` — recompute the runway regression.

Both are designed to be invoked from APScheduler at fixed intervals,
but are also safe to call manually from the CLI for ad-hoc evaluation.
"""

from .dispatcher import evaluate_rules
from .forecast import refresh_storage_forecasts

__all__ = ["evaluate_rules", "refresh_storage_forecasts"]
