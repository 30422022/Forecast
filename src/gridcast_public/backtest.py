from __future__ import annotations

import math
from typing import Any

from gridcast_public.contracts import (
    BacktestFailure,
    BacktestFold,
    BacktestRequest,
    BacktestResponse,
)
from gridcast_public.ports import ForecastProvider


def rolling_backtest(request: BacktestRequest, provider: ForecastProvider) -> BacktestResponse:
    """Evaluate a provider on recent expanding windows without exposing future rows.

    The provider receives only ``history[:cutoff]``. Actual values, raw forecasts,
    and site identifiers are not included in the returned summary.
    """
    history = request.history
    horizon = request.horizon
    minimum = request.min_train_size or max(8, 2 * horizon)
    if len(history) < minimum + horizon:
        raise ValueError("history is too short for the requested training window and horizon")

    step = request.step or horizon
    last_cutoff = len(history) - horizon
    cutoffs = [
        last_cutoff - offset * step
        for offset in range(request.max_folds - 1, -1, -1)
        if last_cutoff - offset * step >= minimum
    ]
    folds: list[BacktestFold] = []
    failures: list[BacktestFailure] = []
    for cutoff in cutoffs:
        try:
            prediction = provider.predict(list(history[:cutoff]), horizon)
            if (
                not isinstance(prediction, list)
                or len(prediction) != horizon
                or any(not _finite(value) or abs(value) > 1e12 for value in prediction)
            ):
                raise ValueError("invalid provider output")
        except Exception:
            # Provider exceptions may contain data or credentials; return a fixed code.
            failures.append(
                BacktestFailure(cutoff_index=cutoff, reason_code="PROVIDER_OUTPUT_INVALID")
            )
            continue
        actual = history[cutoff : cutoff + horizon]
        absolute_errors = [abs(a - p) for a, p in zip(actual, prediction, strict=True)]
        squared_errors = [(a - p) ** 2 for a, p in zip(actual, prediction, strict=True)]
        actual_total = sum(abs(value) for value in actual)
        smape_parts = [
            0.0 if abs(a) + abs(p) == 0 else 2 * abs(a - p) / (abs(a) + abs(p))
            for a, p in zip(actual, prediction, strict=True)
        ]
        folds.append(
            BacktestFold(
                cutoff_index=cutoff,
                train_rows=cutoff,
                test_rows=horizon,
                mae=sum(absolute_errors) / horizon,
                rmse=math.sqrt(sum(squared_errors) / horizon),
                wape=sum(absolute_errors) / actual_total if actual_total > 1e-12 else None,
                smape=sum(smape_parts) / horizon,
            )
        )

    return BacktestResponse(
        provider=provider.name,
        horizon=horizon,
        fold_count=len(cutoffs),
        completed_folds=len(folds),
        folds=folds,
        failures=failures,
        mean_mae=_mean(folds, "mae"),
        mean_rmse=_mean(folds, "rmse"),
        mean_wape=_mean(folds, "wape"),
        mean_smape=_mean(folds, "smape"),
    )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mean(folds: list[BacktestFold], field: str) -> float | None:
    values = [value for fold in folds if (value := getattr(fold, field)) is not None]
    return sum(values) / len(values) if values else None
