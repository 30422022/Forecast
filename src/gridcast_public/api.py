from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gridcast_public.adapters import (
    DisabledDecisionProvider,
    FiniteValueQualityProvider,
    SafeMockForecastProvider,
)
from gridcast_public.backtest import rolling_backtest
from gridcast_public.contracts import (
    BacktestRequest,
    BacktestResponse,
    WorkflowFailureResponse,
    WorkflowReceipt,
    WorkflowRequest,
    WorkflowResponse,
)
from gridcast_public.crypto import CheckpointCryptoError, load_key
from gridcast_public.durable_store import DurableStore
from gridcast_public.executor import DurableExecutor, LeaseLost
from gridcast_public.receipts import ReceiptStore
from gridcast_public.workflow import build_workflow, run_workflow


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.graph = build_workflow(
        quality=FiniteValueQualityProvider(),
        forecast=SafeMockForecastProvider(),
        decision=DisabledDecisionProvider(),
    )
    application.state.receipts = ReceiptStore()
    application.state.durable = None
    application.state.durable_error = None
    checkpoint_key = os.getenv("GRIDCAST_CHECKPOINT_KEY")
    database_url = os.getenv("GRIDCAST_DATABASE_URL")
    if checkpoint_key and database_url:
        try:
            key = load_key(checkpoint_key)
            store = DurableStore(
                database_url, key, schema=os.getenv("GRIDCAST_DB_SCHEMA", "public")
            )
        except CheckpointCryptoError:
            application.state.durable_error = "checkpoint_key_invalid"
        except (psycopg.Error, ValueError):
            application.state.durable_error = "postgres_unavailable"
        else:
            application.state.durable = DurableExecutor(
                store,
                FiniteValueQualityProvider(),
                SafeMockForecastProvider(),
                DisabledDecisionProvider(),
            )
    elif checkpoint_key or database_url:
        application.state.durable_error = "durable_configuration_incomplete"
    yield


app = FastAPI(
    title="GridCast-Agent Public Preview",
    version="0.1.0",
    description="Sanitized orchestration skeleton; no private model or operational decision logic.",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path == "/v1/evaluations/backtest":
        # FastAPI's default 422 body may echo invalid input values.
        return JSONResponse(status_code=422, content={"detail": "invalid backtest request"})
    return await request_validation_exception_handler(request, exc)


@app.get("/health")
async def health() -> dict[str, str]:
    durable = app.state.durable is not None
    return {
        "status": "ok",
        "mode": "public_preview",
        "storage": "postgresql_aes_gcm"
        if durable
        else "unavailable"
        if app.state.durable_error
        else "memory",
        "recovery_available": str(durable).lower(),
    }


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, Any]:
    return {
        "mode": "public_preview",
        "private_assets_loaded": False,
        "forecast_provider": "safe_mock_last_value",
        "decision_provider": "disabled",
        "storage": "postgresql_aes_gcm"
        if app.state.durable is not None
        else "unavailable"
        if app.state.durable_error
        else "memory",
        "recovery_available": app.state.durable is not None,
        "backtest_available": True,
        "limitations": [
            "no production model",
            "no data bundle",
            "no rule corpus",
            "no optimization strategy",
        ],
    }


@app.post("/v1/evaluations/backtest", response_model=BacktestResponse)
async def backtest(request: BacktestRequest) -> BacktestResponse:
    try:
        return await asyncio.to_thread(rolling_backtest, request, SafeMockForecastProvider())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="history is too short for the requested training window and horizon",
        ) from None


@app.post(
    "/v1/workflows/run",
    response_model=WorkflowResponse,
    responses={502: {"model": WorkflowFailureResponse}},
)
async def run(
    request: WorkflowRequest,
    durable: bool = Query(False),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> WorkflowResponse | JSONResponse:
    if durable:
        executor = app.state.durable
        if executor is None:
            raise HTTPException(
                status_code=503,
                detail="durable mode unavailable: PostgreSQL URL and checkpoint key required",
            )
        request_id = ""
        try:
            if idempotency_key is not None:
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
                    raise HTTPException(status_code=422, detail="invalid Idempotency-Key")
                hmac_key = load_key(os.getenv("GRIDCAST_IDEMPOTENCY_KEY"))
                request_body = request.model_dump_json()
                key_hash = hmac.new(hmac_key, idempotency_key.encode(), hashlib.sha256).hexdigest()
                request_hash = hmac.new(hmac_key, request_body.encode(), hashlib.sha256).hexdigest()
                request_id = str(uuid4())
                request_id, _ = executor.store.idempotent_create(
                    key_hash,
                    request_hash,
                    request_id,
                    {
                        "request": request.model_dump(),
                        "trace": [],
                        "messages": [],
                        "outcome": "running",
                        "attempt": 0,
                    },
                )
            else:
                request_id = executor.submit(request)
            result = await asyncio.to_thread(executor.execute, request_id)
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key conflicts with an earlier request"
            ) from None
        except CheckpointCryptoError:
            raise HTTPException(status_code=503, detail="durable key unavailable") from None
        except (LeaseLost, psycopg.Error):
            return JSONResponse(
                status_code=503,
                content={
                    "request_id": request_id,
                    "status": "recoverable",
                    "error_code": "DURABLE_EXECUTION_INTERRUPTED",
                },
            )
        if result is None:
            try:
                status = executor.store.status(request_id)
            except psycopg.Error:
                raise HTTPException(status_code=503, detail="durable status unavailable") from None
            running = status is not None and status.status == "running"
            return JSONResponse(
                status_code=409 if running else 202,
                content={
                    "request_id": request_id,
                    "status": status.status if status is not None else "recoverable",
                    "error_code": "RUN_IN_PROGRESS" if running else None,
                },
            )
        if isinstance(result, WorkflowFailureResponse):
            return JSONResponse(status_code=502, content=result.model_dump(mode="json"))
        return result
    result = await asyncio.to_thread(run_workflow, app.state.graph, request)
    app.state.receipts.record(result)
    if isinstance(result, WorkflowFailureResponse):
        return JSONResponse(status_code=502, content=result.model_dump(mode="json"))
    return result


@app.get("/v1/workflows/receipts/{request_id}", response_model=WorkflowReceipt)
async def get_receipt(request_id: str) -> WorkflowReceipt:
    try:
        UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="execution receipt not found") from None
    if app.state.durable is not None:
        try:
            status = app.state.durable.store.status(request_id)
        except psycopg.Error:
            raise HTTPException(status_code=503, detail="durable receipt unavailable") from None
        if status is not None:
            return WorkflowReceipt(
                request_id=request_id,
                status=status.status,
                last_node=status.messages[-1].sender if status.messages else "queued",
                error_code=status.error_code,
                created_at=status.created_at,
                expires_at=None,
            )
    receipt = app.state.receipts.get(request_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="execution receipt not found")
    return receipt


@app.get("/v1/workflows/{request_id}")
async def durable_status(request_id: str) -> dict[str, Any]:
    executor = app.state.durable
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="durable mode unavailable: PostgreSQL URL and checkpoint key required",
        )
    try:
        UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="execution not found") from None
    try:
        status = executor.store.status(request_id)
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="durable status unavailable") from None
    if status is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return status.model_dump(mode="json")


@app.post("/v1/workflows/{request_id}/resume", response_model=None)
async def resume(request_id: str) -> WorkflowResponse | JSONResponse:
    executor = app.state.durable
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="durable mode unavailable: PostgreSQL URL and checkpoint key required",
        )
    try:
        UUID(request_id)
        result = await asyncio.to_thread(executor.execute, request_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="execution not found") from None
    except CheckpointCryptoError:
        raise HTTPException(status_code=409, detail="checkpoint recovery failed") from None
    except (LeaseLost, psycopg.Error):
        return JSONResponse(
            status_code=503,
            content={
                "request_id": request_id,
                "status": "recoverable",
                "error_code": "DURABLE_EXECUTION_INTERRUPTED",
            },
        )
    if result is None:
        try:
            status = executor.store.status(request_id)
        except psycopg.Error:
            raise HTTPException(status_code=503, detail="durable status unavailable") from None
        if status is None:
            raise HTTPException(status_code=404, detail="execution not found")
        return JSONResponse(
            status_code=409,
            content={
                "request_id": request_id,
                "status": status.status,
                "error_code": "RUN_IN_PROGRESS",
            },
        )
    if isinstance(result, WorkflowFailureResponse):
        return JSONResponse(status_code=502, content=result.model_dump(mode="json"))
    return result
