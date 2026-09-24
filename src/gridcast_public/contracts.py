from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class WorkflowRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=128)
    history: list[float] = Field(min_length=2, max_length=10_000)
    horizon: int = Field(gt=0, le=672)

    @field_validator("history")
    @classmethod
    def finite_history(cls, values: list[float]) -> list[float]:
        if any(value != value or value in {float("inf"), float("-inf")} for value in values):
            raise ValueError("history must contain finite values")
        return values


class BacktestRequest(BaseModel):
    history: list[float] = Field(min_length=2, max_length=10_000)
    horizon: int = Field(ge=1, le=672)
    max_folds: int = Field(default=5, ge=1, le=12)
    step: int | None = Field(default=None, ge=1, le=672)
    min_train_size: int | None = Field(default=None, ge=2, le=9_999)

    @field_validator("history")
    @classmethod
    def finite_history(cls, values: list[float]) -> list[float]:
        if any(value != value or value in {float("inf"), float("-inf")} for value in values):
            raise ValueError("history must contain finite values")
        if any(abs(value) > 1e12 for value in values):
            raise ValueError("backtest values must be within +/- 1e12")
        return values


class BacktestFold(BaseModel):
    cutoff_index: int
    train_rows: int
    test_rows: int
    mae: float
    rmse: float
    wape: float | None
    smape: float


class BacktestFailure(BaseModel):
    cutoff_index: int
    reason_code: Literal["PROVIDER_OUTPUT_INVALID"]


class BacktestResponse(BaseModel):
    demo_only: Literal[True] = True
    method: Literal["rolling_origin_expanding_window"] = "rolling_origin_expanding_window"
    provider: str
    horizon: int
    fold_count: int
    completed_folds: int
    folds: list[BacktestFold]
    failures: list[BacktestFailure]
    mean_mae: float | None
    mean_rmse: float | None
    mean_wape: float | None
    mean_smape: float | None


class TraceStep(BaseModel):
    node: str
    status: Literal["succeeded", "skipped", "failed"]
    detail: str = ""


class WorkflowResponse(BaseModel):
    request_id: str
    status: Literal["demo_only"] = "demo_only"
    forecast: list[float]
    decision: dict[str, Any]
    trace: list[TraceStep]
    provenance: dict[str, Any]


class WorkflowFailureResponse(BaseModel):
    request_id: str
    status: Literal["failed"] = "failed"
    error_code: Literal[
        "QUALITY_PROVIDER_ERROR",
        "FORECAST_PROVIDER_ERROR",
        "FORECAST_OUTPUT_INVALID",
        "DECISION_PROVIDER_ERROR",
    ]
    failed_node: Literal["quality", "forecast", "decision"]
    trace: list[TraceStep]
    provenance: dict[str, Any]


class WorkflowReceipt(BaseModel):
    request_id: str
    status: Literal["queued", "running", "recoverable", "succeeded", "failed"]
    last_node: str
    error_code: str | None = None
    created_at: str
    expires_at: str | None = None


class AgentMessage(BaseModel):
    message_id: str
    request_id: str
    sender: Literal["quality", "forecast", "review", "decision", "skip_decision", "audit"]
    recipient: Literal["quality", "forecast", "review", "decision", "skip_decision", "audit"]
    kind: Literal["candidate", "review", "decision", "skip", "failure", "audit"]
    attempt: int = Field(ge=0, le=1)
    verdict: Literal["approved", "revise", "skipped", "failed", "complete"] | None = None
    reason_code: Literal["FORECAST_OUTPUT_INVALID", "PROVIDER_ERROR", ""] = ""
    created_at: str


class DurableStatus(BaseModel):
    request_id: str
    status: Literal["queued", "running", "recoverable", "succeeded", "failed"]
    next_stage: str | None
    attempt: int
    error_code: str | None = None
    created_at: str
    updated_at: str
    messages: list[AgentMessage] = Field(default_factory=list)
