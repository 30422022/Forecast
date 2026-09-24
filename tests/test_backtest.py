from __future__ import annotations

from fastapi.testclient import TestClient

from gridcast_public.api import app
from gridcast_public.backtest import rolling_backtest
from gridcast_public.contracts import BacktestRequest


class RecordingProvider:
    name = "recording_demo"

    def __init__(self) -> None:
        self.inputs: list[list[float]] = []

    def predict(self, history: list[float], horizon: int) -> list[float]:
        self.inputs.append(history)
        return [history[-1]] * horizon


def test_recent_folds_never_expose_future_rows_to_provider() -> None:
    provider = RecordingProvider()
    request = BacktestRequest(history=list(range(1, 25)), horizon=2, max_folds=3, step=4)
    result = rolling_backtest(request, provider)
    assert [fold.cutoff_index for fold in result.folds] == [14, 18, 22]
    assert provider.inputs == [list(range(1, cutoff + 1)) for cutoff in (14, 18, 22)]
    assert result.fold_count == result.completed_folds == 3
    assert result.folds[-1].mae == 1.5
    assert result.mean_wape is not None
    assert "history" not in result.model_dump()


class InvalidProvider:
    name = "invalid_demo"

    def predict(self, history: list[float], horizon: int) -> list[float]:
        raise RuntimeError("private input should never appear in the API")


def test_provider_failures_only_return_fixed_reason_code() -> None:
    request = BacktestRequest(history=list(range(16)), horizon=2, max_folds=2)
    result = rolling_backtest(request, InvalidProvider())
    assert result.completed_folds == 0
    assert result.mean_mae is None
    assert {failure.reason_code for failure in result.failures} == {"PROVIDER_OUTPUT_INVALID"}
    assert "private input" not in result.model_dump_json()


def test_api_backtest_validates_length_and_returns_only_metrics() -> None:
    with TestClient(app) as client:
        short = client.post("/v1/evaluations/backtest", json={"history": [1, 2], "horizon": 2})
        assert short.status_code == 422
        response = client.post(
            "/v1/evaluations/backtest",
            json={"history": list(range(24)), "horizon": 2, "max_folds": 3},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["provider"] == "safe_mock_last_value"
        assert payload["completed_folds"] == 3
        assert all("forecast" not in fold and "actual" not in fold for fold in payload["folds"])


def test_zero_actuals_have_undefined_wape_and_invalid_input_is_not_echoed() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/evaluations/backtest", json={"history": [0] * 12, "horizon": 2}
        )
        assert response.status_code == 200
        assert response.json()["mean_wape"] is None
        assert response.json()["mean_smape"] == 0
        invalid = client.post(
            "/v1/evaluations/backtest", json={"history": [1e13] * 12, "horizon": 2}
        )
        assert invalid.status_code == 422
        assert "10000000000000" not in invalid.text
