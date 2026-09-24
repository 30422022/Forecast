from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg

from gridcast_public.contracts import (
    AgentMessage,
    TraceStep,
    WorkflowFailureResponse,
    WorkflowRequest,
    WorkflowResponse,
)
from gridcast_public.durable_store import DurableStore
from gridcast_public.ports import DecisionProvider, ForecastProvider, QualityProvider

STAGES = ("quality", "forecast", "review", "decision", "audit")


class LeaseLost(RuntimeError):
    pass


class DurableExecutor:
    def __init__(
        self,
        store: DurableStore,
        quality: QualityProvider,
        forecast: ForecastProvider,
        decision: DecisionProvider,
    ):
        self.store, self.quality, self.forecast, self.decision = store, quality, forecast, decision

    def submit(self, request: WorkflowRequest, request_id: str | None = None) -> str:
        rid = request_id or str(uuid4())
        self.store.create(
            rid,
            {
                "request": request.model_dump(),
                "trace": [],
                "messages": [],
                "outcome": "running",
                "attempt": 0,
            },
        )
        return rid

    def execute(
        self, request_id: str, max_stages: int | None = None
    ) -> WorkflowResponse | WorkflowFailureResponse | None:
        owner = str(uuid4())
        fence = self.store.claim(request_id, owner)
        if fence is None:
            current = self.store.read(request_id)
            if current and current[1]["status"] in {"succeeded", "failed"}:
                return self._response(request_id, current[0])
            return None
        count = 0
        try:
            while True:
                loaded = self.store.read(request_id)
                if loaded is None:
                    return None
                state, row = loaded
                stage = row["next_stage"]
                if stage is None:
                    return self._response(request_id, state)
                if not self.store.renew(request_id, owner, fence):
                    raise LeaseLost("lease lost")
                next_stage, status = self._stage_with_heartbeat(
                    request_id, state, stage, owner, fence
                )
                messages = [AgentMessage.model_validate(item) for item in state["messages"]]
                if not self.store.commit_stage(
                    request_id,
                    owner,
                    fence,
                    state=state,
                    next_stage=next_stage,
                    status=status,
                    attempt=state["attempt"],
                    error_code=state.get("error_code"),
                    messages=messages,
                ):
                    raise LeaseLost("lease lost")
                count += 1
                if status in {"succeeded", "failed"}:
                    return self._response(request_id, state)
                if max_stages is not None and count >= max_stages:
                    self.store.release(request_id, owner, fence)
                    return None
        except BaseException:
            self.store.release(request_id, owner, fence)
            raise

    def _stage_with_heartbeat(
        self, request_id: str, state: dict[str, Any], stage: str, owner: str, fence: int
    ) -> tuple[str | None, str]:
        stopped = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            interval = max(0.01, self.store.lease_seconds / 3)
            while not stopped.wait(interval):
                try:
                    if not self.store.renew(request_id, owner, fence):
                        lost.set()
                        return
                except psycopg.Error:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = self._stage(request_id, state, stage)
        finally:
            stopped.set()
            thread.join()
        if lost.is_set():
            raise LeaseLost("lease lost")
        return result

    def _stage(self, rid: str, s: dict[str, Any], stage: str) -> tuple[str | None, str]:
        agents = {
            "quality": QualityAgent(),
            "forecast": ForecastAgent(),
            "review": ReviewAgent(),
            "decision": DecisionAgent(),
            "audit": AuditAgent(),
        }
        agent = agents.get(stage)
        if agent is None:
            raise ValueError("invalid stage")
        return agent.run(self, rid, s)

    def _quality_stage(self, rid: str, s: dict[str, Any]) -> tuple[str, str]:
        request = WorkflowRequest.model_validate(s["request"])
        attempt = s["attempt"]
        try:
            result = self.quality.validate(request.history)
            if (
                not isinstance(result, tuple)
                or len(result) != 2
                or not isinstance(result[0], list)
                or len(result[0]) < 2
                or not isinstance(result[1], list)
                or any(not isinstance(item, str) for item in result[1])
                or any(not _finite(x) for x in result[0])
            ):
                raise ValueError
            s["clean_history"], s["warnings"] = result
            self._trace(s, "quality", "succeeded", "provider")
            self._message(s, rid, "quality", "forecast", "candidate", attempt)
            return "forecast", "running"
        except Exception:
            return self._fail(rid, s, "quality", "QUALITY_PROVIDER_ERROR", attempt)

    def _forecast_stage(self, rid: str, s: dict[str, Any]) -> tuple[str, str]:
        request = WorkflowRequest.model_validate(s["request"])
        attempt = s["attempt"]
        try:
            values = self.forecast.predict(s["clean_history"], request.horizon)
            if (
                not isinstance(values, list)
                or len(values) != request.horizon
                or any(not _finite(x) for x in values)
            ):
                s["candidate"] = []
                s["candidate_valid"] = False
            else:
                s["candidate"] = [float(x) for x in values]
                s["candidate_valid"] = True
        except Exception:
            return self._fail(rid, s, "forecast", "FORECAST_PROVIDER_ERROR", attempt)
        self._trace(s, "forecast", "succeeded", "provider")
        self._message(s, rid, "forecast", "review", "candidate", attempt)
        return "review", "running"

    def _review_stage(self, rid: str, s: dict[str, Any]) -> tuple[str, str]:
        attempt = s["attempt"]
        if s.pop("candidate_valid", False):
            s["forecast"] = s.pop("candidate")
            self._trace(s, "review", "succeeded", "contract_approved")
            self._message(s, rid, "review", "decision", "review", attempt, "approved")
            return "decision", "running"
        s.pop("candidate", None)
        if attempt < 1:
            s["attempt"] = attempt + 1
            self._trace(s, "review", "succeeded", "contract_revise")
            self._message(
                s, rid, "review", "forecast", "review", attempt, "revise", "FORECAST_OUTPUT_INVALID"
            )
            return "forecast", "running"
        self._trace(s, "review", "failed", "contract_invalid")
        self._message(
            s,
            rid,
            "review",
            "audit",
            "failure",
            attempt,
            "failed",
            "FORECAST_OUTPUT_INVALID",
        )
        return self._fail(rid, s, "forecast", "FORECAST_OUTPUT_INVALID", attempt)

    def _decision_stage(self, rid: str, s: dict[str, Any]) -> tuple[str, str]:
        attempt = s["attempt"]
        if not self.decision.enabled:
            s["decision"] = {
                "status": "adapter_not_configured",
                "message": "Private decision provider is not included in the public preview.",
                "actionable": False,
            }
            self._trace(s, "decision", "skipped", "disabled")
            self._message(s, rid, "decision", "audit", "skip", attempt, "skipped")
            return "audit", "running"
        try:
            advice = self.decision.advise(s["forecast"])
            json.dumps(advice, allow_nan=False)
            if not isinstance(advice, dict):
                raise ValueError
            s["decision"] = advice
        except Exception:
            return self._fail(rid, s, "decision", "DECISION_PROVIDER_ERROR", attempt)
        self._trace(s, "decision", "succeeded", "provider")
        self._message(s, rid, "decision", "audit", "decision", attempt)
        return "audit", "running"

    def _audit_stage(self, rid: str, s: dict[str, Any]) -> tuple[None, str]:
        attempt = s["attempt"]
        self._trace(s, "audit", "succeeded", "execution_metadata_sha256")
        evidence = {
            "request_id": rid,
            "outcome": s["outcome"],
            "nodes": [{"node": t["node"], "status": t["status"]} for t in s["trace"]],
            "error_code": s.get("error_code"),
        }
        s["fingerprint"] = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._message(s, rid, "audit", "audit", "audit", attempt, "complete")
        return None, "failed" if s["outcome"] == "failed" else "succeeded"

    def _fail(
        self, rid: str, s: dict[str, Any], node: str, code: str, attempt: int
    ) -> tuple[str, str]:
        s["outcome"], s["error_code"], s["failed_node"] = "failed", code, node
        self._trace(
            s,
            node,
            "failed",
            "provider_error" if code.endswith("PROVIDER_ERROR") else "contract_error",
        )
        self._message(
            s,
            rid,
            node,
            "audit",
            "failure",
            attempt,
            "failed",
            "PROVIDER_ERROR" if code.endswith("PROVIDER_ERROR") else code,
        )
        return "audit", "running"

    @staticmethod
    def _trace(s: dict[str, Any], node: str, status: str, detail: str) -> None:
        s["trace"].append({"node": node, "status": status, "detail": detail})

    @staticmethod
    def _message(
        s: dict[str, Any],
        rid: str,
        sender: str,
        recipient: str,
        kind: str,
        attempt: int,
        verdict: str | None = None,
        reason: str = "",
    ) -> None:
        s["messages"].append(
            AgentMessage(
                message_id=str(uuid4()),
                request_id=rid,
                sender=sender,
                recipient=recipient,
                kind=kind,
                attempt=attempt,
                verdict=verdict,
                reason_code=reason,
                created_at=datetime.now(UTC).isoformat(),
            ).model_dump()
        )

    @staticmethod
    def _response(rid: str, s: dict[str, Any]) -> WorkflowResponse | WorkflowFailureResponse:
        provenance = {
            "demo_only": True,
            "fingerprint_algorithm": "sha256",
            "fingerprint": s["fingerprint"],
            "fingerprint_scope": "execution_metadata_only",
            "private_assets_loaded": False,
        }
        trace = [TraceStep.model_validate(item) for item in s["trace"]]
        if s["outcome"] == "failed":
            return WorkflowFailureResponse(
                request_id=rid,
                error_code=s["error_code"],
                failed_node=s["failed_node"],
                trace=trace,
                provenance=provenance,
            )
        return WorkflowResponse(
            request_id=rid,
            forecast=s["forecast"],
            decision=s["decision"],
            trace=trace,
            provenance=provenance,
        )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class QualityAgent:
    def run(self, executor: DurableExecutor, request_id: str, state: dict[str, Any]):
        return executor._quality_stage(request_id, state)


class ForecastAgent:
    def run(self, executor: DurableExecutor, request_id: str, state: dict[str, Any]):
        return executor._forecast_stage(request_id, state)


class ReviewAgent:
    def run(self, executor: DurableExecutor, request_id: str, state: dict[str, Any]):
        return executor._review_stage(request_id, state)


class DecisionAgent:
    def run(self, executor: DurableExecutor, request_id: str, state: dict[str, Any]):
        return executor._decision_stage(request_id, state)


class AuditAgent:
    def run(self, executor: DurableExecutor, request_id: str, state: dict[str, Any]):
        return executor._audit_stage(request_id, state)
