from gridcast_public.adapters import (
    DisabledDecisionProvider,
    FiniteValueQualityProvider,
    SafeMockForecastProvider,
)
from gridcast_public.contracts import WorkflowFailureResponse, WorkflowRequest
from gridcast_public.workflow import build_workflow, run_workflow


class FailingQuality:
    name = "quality_test"

    def validate(self, history: list[float]) -> tuple[list[float], list[str]]:
        del history
        raise RuntimeError("SECRET_INPUT quality provider traceback")


class InvalidQuality:
    name = "quality_test"

    def __init__(self, clean):
        self.clean = clean

    def validate(self, history: list[float]) -> tuple[list[float], list[str]]:
        del history
        return self.clean, []


class FailingForecast:
    name = "forecast_test"

    def __init__(self, values=None, fail=False):
        self.values = values
        self.fail = fail

    def predict(self, history: list[float], horizon: int) -> list[float]:
        del history, horizon
        if self.fail:
            raise RuntimeError("SECRET_FORECAST private exception")
        return self.values


class EnabledDecision:
    name = "decision_test"
    enabled = True

    def __init__(self, fail=False):
        self.fail = fail

    def advise(self, forecast: list[float]) -> dict:
        if self.fail:
            raise RuntimeError(f"SECRET_DECISION {forecast}")
        return {"status": "ok", "actionable": False}


def request() -> WorkflowRequest:
    return WorkflowRequest(site_id="PRIVATE_SITE", history=[11.0, 22.0, 33.0], horizon=2)


def execute(quality=None, forecast=None, decision=None):
    graph = build_workflow(
        quality or FiniteValueQualityProvider(),
        forecast or SafeMockForecastProvider(),
        decision or DisabledDecisionProvider(),
    )
    return run_workflow(graph, request())


def test_public_workflow_skips_disabled_decision_and_is_demo_only() -> None:
    response = execute()

    assert response.status == "demo_only"
    assert response.forecast == [33.0, 33.0]
    assert response.decision["actionable"] is False
    assert response.provenance["private_assets_loaded"] is False
    assert response.provenance["fingerprint_scope"] == "execution_metadata_only"
    assert len(response.provenance["fingerprint"]) == 64
    assert [(step.node, step.status) for step in response.trace] == [
        ("quality", "succeeded"),
        ("forecast", "succeeded"),
        ("decision", "skipped"),
        ("audit", "succeeded"),
    ]


def test_enabled_decision_runs_and_reports_success() -> None:
    response = execute(decision=EnabledDecision())
    assert response.decision["status"] == "ok"
    assert [(step.node, step.status) for step in response.trace][-2:] == [
        ("decision", "succeeded"),
        ("audit", "succeeded"),
    ]


def test_quality_failure_routes_to_audit_without_leaking_exception_or_request() -> None:
    response = execute(quality=FailingQuality())
    assert isinstance(response, WorkflowFailureResponse)
    assert response.error_code == "QUALITY_PROVIDER_ERROR"
    assert response.failed_node == "quality"
    assert [step.node for step in response.trace] == ["quality", "audit"]
    payload = response.model_dump_json()
    for secret in ("SECRET_INPUT", "PRIVATE_SITE", "11.0", "forecast", "decision"):
        assert secret not in payload


def test_invalid_quality_result_is_classified_at_quality_boundary() -> None:
    for clean in ([], ["not-a-number", 2.0], [True, 2.0], [float("nan"), 2.0]):
        response = execute(quality=InvalidQuality(clean))
        assert isinstance(response, WorkflowFailureResponse)
        assert response.error_code == "QUALITY_PROVIDER_ERROR"
        assert response.failed_node == "quality"
        assert [step.node for step in response.trace] == ["quality", "audit"]


def test_forecast_provider_failure_is_fixed_and_audited() -> None:
    response = execute(forecast=FailingForecast(fail=True))
    assert isinstance(response, WorkflowFailureResponse)
    assert response.error_code == "FORECAST_PROVIDER_ERROR"
    assert [step.node for step in response.trace] == ["quality", "forecast", "audit"]
    assert "SECRET_FORECAST" not in response.model_dump_json()


def test_invalid_forecast_lengths_and_nonfinite_values_take_safe_failure_path() -> None:
    for values in ([1.0], [float("nan"), 2.0], [float("inf"), 2.0], [-float("inf"), 2.0]):
        response = execute(forecast=FailingForecast(values=values))
        assert isinstance(response, WorkflowFailureResponse)
        assert response.error_code == "FORECAST_OUTPUT_INVALID"
        assert response.failed_node == "forecast"
        assert [step.node for step in response.trace] == ["quality", "forecast", "audit"]
        payload = response.model_dump_json()
        assert "1.0" not in payload
        assert "Infinity" not in payload
        assert "NaN" not in payload


def test_enabled_decision_failure_does_not_return_forecast_or_exception() -> None:
    response = execute(decision=EnabledDecision(fail=True))
    assert isinstance(response, WorkflowFailureResponse)
    assert response.error_code == "DECISION_PROVIDER_ERROR"
    assert [step.node for step in response.trace] == [
        "quality",
        "forecast",
        "decision",
        "audit",
    ]
    payload = response.model_dump_json()
    assert "SECRET_DECISION" not in payload
    assert "33.0" not in payload
