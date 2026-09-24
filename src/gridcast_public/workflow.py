from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from gridcast_public.contracts import (
    TraceStep,
    WorkflowFailureResponse,
    WorkflowRequest,
    WorkflowResponse,
)
from gridcast_public.ports import DecisionProvider, ForecastProvider, QualityProvider


class PublicAgentState(TypedDict, total=False):
    request: WorkflowRequest
    request_id: str
    clean_history: list[float]
    warnings: list[str]
    forecast: list[float]
    decision: dict[str, Any]
    trace: list[dict[str, str]]
    outcome: Literal["running", "succeeded", "failed"]
    error_code: str | None
    failed_node: str | None
    fingerprint: str


def build_workflow(
    quality: QualityProvider,
    forecast: ForecastProvider,
    decision: DecisionProvider,
) -> Any:
    """Build a public graph whose private capabilities are injected as providers."""

    def add_trace(
        state: PublicAgentState, node: str, status: str, detail: str
    ) -> list[dict[str, str]]:
        return [*state.get("trace", []), {"node": node, "status": status, "detail": detail}]

    def failure(state: PublicAgentState, node: str, error_code: str) -> dict[str, Any]:
        return {
            "outcome": "failed",
            "error_code": error_code,
            "failed_node": node,
            "trace": add_trace(state, node, "failed", "provider_error"),
        }

    def quality_node(state: PublicAgentState) -> dict[str, Any]:
        try:
            result = quality.validate(state["request"].history)
            if (
                not isinstance(result, tuple)
                or len(result) != 2
                or not isinstance(result[0], list)
                or len(result[0]) < 2
                or not isinstance(result[1], list)
                or any(not isinstance(item, str) for item in result[1])
                or any(not _is_finite_number(value) for value in result[0])
            ):
                return failure(state, "quality", "QUALITY_PROVIDER_ERROR")
            clean, warnings = result
        except Exception:
            return failure(state, "quality", "QUALITY_PROVIDER_ERROR")
        return {
            "clean_history": clean,
            "warnings": warnings,
            "outcome": "running",
            "trace": add_trace(state, "quality", "succeeded", str(quality.name)),
        }

    def forecast_node(state: PublicAgentState) -> dict[str, Any]:
        try:
            values = forecast.predict(state["clean_history"], state["request"].horizon)
        except Exception:
            return failure(state, "forecast", "FORECAST_PROVIDER_ERROR")
        if (
            not isinstance(values, list)
            or len(values) != state["request"].horizon
            or any(not _is_finite_number(value) for value in values)
        ):
            return failure(state, "forecast", "FORECAST_OUTPUT_INVALID")
        numeric_values = [float(value) for value in values]
        return {
            "forecast": numeric_values,
            "outcome": "running",
            "trace": add_trace(state, "forecast", "succeeded", str(forecast.name)),
        }

    def decision_node(state: PublicAgentState) -> dict[str, Any]:
        try:
            advice = decision.advise(state["forecast"])
            if not isinstance(advice, dict):
                return failure(state, "decision", "DECISION_PROVIDER_ERROR")
            # Ensure arbitrary provider objects cannot break response serialization.
            json.dumps(advice, allow_nan=False)
        except Exception:
            return failure(state, "decision", "DECISION_PROVIDER_ERROR")
        return {
            "decision": advice,
            "outcome": "running",
            "trace": add_trace(state, "decision", "succeeded", str(decision.name)),
        }

    def skip_decision_node(state: PublicAgentState) -> dict[str, Any]:
        return {
            "decision": {
                "status": "adapter_not_configured",
                "message": "Private decision provider is not included in the public preview.",
                "actionable": False,
            },
            "outcome": "running",
            "trace": add_trace(state, "decision", "skipped", str(decision.name)),
        }

    def audit_node(state: PublicAgentState) -> dict[str, Any]:
        trace = add_trace(state, "audit", "succeeded", "execution_metadata_sha256")
        evidence = {
            "request_id": state["request_id"],
            "outcome": state.get("outcome", "succeeded"),
            "nodes": [{"node": step["node"], "status": step["status"]} for step in trace],
            "error_code": state.get("error_code"),
        }
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        return {"fingerprint": hashlib.sha256(encoded).hexdigest(), "trace": trace}

    def route_outcome(state: PublicAgentState) -> str:
        return "audit" if state.get("outcome") == "failed" else "continue"

    def route_after_forecast(state: PublicAgentState) -> str:
        if state.get("outcome") == "failed":
            return "audit"
        return "decision" if decision.enabled else "skip_decision"

    graph = StateGraph(PublicAgentState)
    graph.add_node("quality", quality_node)
    graph.add_node("forecast", forecast_node)
    graph.add_node("decision", decision_node)
    graph.add_node("skip_decision", skip_decision_node)
    graph.add_node("audit", audit_node)
    graph.add_edge(START, "quality")
    graph.add_conditional_edges(
        "quality", route_outcome, {"audit": "audit", "continue": "forecast"}
    )
    graph.add_conditional_edges(
        "forecast",
        route_after_forecast,
        {"audit": "audit", "decision": "decision", "skip_decision": "skip_decision"},
    )
    graph.add_conditional_edges("decision", route_outcome, {"audit": "audit", "continue": "audit"})
    graph.add_edge("skip_decision", "audit")
    graph.add_edge("audit", END)
    return graph.compile()


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def run_workflow(
    graph: Any, request: WorkflowRequest
) -> WorkflowResponse | WorkflowFailureResponse:
    request_id = str(uuid4())
    state = graph.invoke(
        {"request": request, "request_id": request_id, "trace": [], "outcome": "running"}
    )
    provenance = {
        "demo_only": True,
        "fingerprint_algorithm": "sha256",
        "fingerprint": state["fingerprint"],
        "fingerprint_scope": "execution_metadata_only",
        "private_assets_loaded": False,
    }
    trace = [TraceStep.model_validate(step) for step in state["trace"]]
    if state.get("outcome") == "failed":
        return WorkflowFailureResponse(
            request_id=request_id,
            error_code=state["error_code"],
            failed_node=state["failed_node"],
            trace=trace,
            provenance=provenance,
        )
    return WorkflowResponse(
        request_id=request_id,
        forecast=state["forecast"],
        decision=state["decision"],
        trace=trace,
        provenance=provenance,
    )
