from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from gridcast_public.adapters import (
    DisabledDecisionProvider,
    FiniteValueQualityProvider,
    SafeMockForecastProvider,
)
from gridcast_public.contracts import WorkflowRequest
from gridcast_public.crypto import CheckpointCryptoError
from gridcast_public.durable_store import DurableStore
from gridcast_public.executor import DurableExecutor

KEY = bytes(range(32))


def executor(
    pg_database, *, forecast=None, lease_seconds: float = 30.0
) -> DurableExecutor:
    dsn, schema = pg_database
    return DurableExecutor(
        DurableStore(dsn, KEY, lease_seconds=lease_seconds, schema=schema),
        FiniteValueQualityProvider(),
        forecast or SafeMockForecastProvider(),
        DisabledDecisionProvider(),
    )


def child_env(pg_database, request_id: str) -> dict[str, str]:
    dsn, schema = pg_database
    env = os.environ.copy()
    env["TEST_DSN"] = dsn
    env["TEST_SCHEMA"] = schema
    env["TEST_REQUEST"] = request_id
    env["GRIDCAST_CHECKPOINT_KEY"] = base64.b64encode(KEY).decode()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    return env


def test_independent_process_resumes_after_committed_quality_stage(pg_database) -> None:
    first = executor(pg_database)
    request = WorkflowRequest(site_id="SENTINEL_SITE", history=[781.25, 991.5], horizon=3)
    request_id = first.submit(request)
    assert first.execute(request_id, max_stages=1) is None
    assert first.store.status(request_id).next_stage == "forecast"

    script = """
import json, os
from gridcast_public.adapters import (
    DisabledDecisionProvider, FiniteValueQualityProvider, SafeMockForecastProvider
)
from gridcast_public.crypto import load_key
from gridcast_public.durable_store import DurableStore
from gridcast_public.executor import DurableExecutor
store = DurableStore(
    os.environ['TEST_DSN'], load_key(), schema=os.environ['TEST_SCHEMA']
)
worker = DurableExecutor(
    store, FiniteValueQualityProvider(), SafeMockForecastProvider(), DisabledDecisionProvider()
)
result = worker.execute(os.environ['TEST_REQUEST'], max_stages=1)
print(json.dumps({
    "result": result.model_dump(mode='json') if result else None,
    "status": store.status(os.environ['TEST_REQUEST']).model_dump(mode='json')
}))
"""
    env = child_env(pg_database, request_id)
    payload = None
    for expected_stage in ("review", "decision", "audit", None):
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
        )
        progress = json.loads(result.stdout)
        if expected_stage is not None:
            assert progress["status"]["next_stage"] == expected_stage
            assert progress["result"] is None
        else:
            payload = progress["result"]
            assert progress["status"]["status"] == "succeeded"
    assert payload["status"] == "demo_only"
    assert payload["forecast"] == [991.5, 991.5, 991.5]
    assert [step["node"] for step in payload["trace"]] == [
        "quality",
        "forecast",
        "review",
        "decision",
        "audit",
    ]
    assert first.store.status(request_id).status == "succeeded"
    with first.store.connect() as db:
        row = db.execute("SELECT * FROM runs WHERE request_id=%s", (request_id,)).fetchone()
    metadata = {key: value for key, value in row.items() if key not in {"nonce", "ciphertext"}}
    assert "SENTINEL_SITE" not in str(metadata)
    assert b"SENTINEL_SITE" not in bytes(row["ciphertext"])
    assert b"781.25" not in bytes(row["ciphertext"])


def test_invalid_candidate_has_one_revision_then_audited_failure(pg_database) -> None:
    class InvalidThenInvalid(SafeMockForecastProvider):
        calls = 0

        def predict(self, history, horizon):
            self.calls += 1
            return [float("nan")] * horizon

    provider = InvalidThenInvalid()
    worker = executor(pg_database, forecast=provider)
    rid = worker.submit(WorkflowRequest(site_id="x", history=[1, 2], horizon=2))
    result = worker.execute(rid)
    assert result.status == "failed"
    assert result.error_code == "FORECAST_OUTPUT_INVALID"
    assert provider.calls == 2
    assert [step.node for step in result.trace].count("review") == 2


def test_wrong_key_fails_closed_and_claim_is_single_owner(pg_database) -> None:
    dsn, schema = pg_database
    store = DurableStore(dsn, KEY, lease_seconds=60, schema=schema)
    rid = str(uuid.uuid4())
    state = {
        "request": {"site_id": "hidden", "history": [1, 2], "horizon": 1},
        "trace": [],
        "messages": [],
        "attempt": 0,
    }
    store.create(rid, state)
    assert store.claim(rid, "owner-a") == 1
    assert store.claim(rid, "owner-b") is None
    with store.connect() as db:
        cipher_before = db.execute(
            "SELECT ciphertext FROM runs WHERE request_id=%s", (rid,)
        ).fetchone()["ciphertext"]
    with pytest.raises(CheckpointCryptoError):
        DurableStore(dsn, bytes(reversed(KEY)), schema=schema).read(rid)
    with store.connect() as db:
        cipher_after = db.execute(
            "SELECT ciphertext FROM runs WHERE request_id=%s", (rid,)
        ).fetchone()["ciphertext"]
    assert cipher_after == cipher_before

    script = """
import os
from gridcast_public.durable_store import DurableStore
store = DurableStore(
    os.environ['TEST_DSN'], bytes(range(32)), schema=os.environ['TEST_SCHEMA']
)
print(store.claim(os.environ['TEST_REQUEST'], 'other'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=child_env(pg_database, rid),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "None"


def test_expired_lease_fencing_rejects_old_owner(pg_database) -> None:
    dsn, schema = pg_database
    store = DurableStore(dsn, KEY, lease_seconds=0.04, schema=schema)
    rid = str(uuid.uuid4())
    store.create(
        rid,
        {
            "request": {"site_id": "x", "history": [1, 2], "horizon": 1},
            "trace": [],
            "messages": [],
            "attempt": 0,
        },
    )
    old_fence = store.claim(rid, "old")
    time.sleep(0.08)
    new_fence = store.claim(rid, "new")
    assert new_fence == old_fence + 1
    current, _ = store.read(rid)
    assert not store.commit_stage(
        rid,
        "old",
        old_fence,
        state=current,
        next_stage="forecast",
        status="running",
        attempt=0,
        error_code=None,
        messages=[],
    )


def test_provider_longer_than_lease_is_renewed(pg_database) -> None:
    class SlowForecast(SafeMockForecastProvider):
        def predict(self, history, horizon):
            time.sleep(2.0)
            return super().predict(history, horizon)

    # The provider still outlives the lease, so success requires heartbeats.
    # A sub-second lease can expire between PostgreSQL calls on a busy CI runner.
    worker = executor(pg_database, forecast=SlowForecast(), lease_seconds=1.0)
    rid = worker.submit(WorkflowRequest(site_id="x", history=[1, 2], horizon=2))
    assert worker.execute(rid).status == "demo_only"


def test_terminal_resume_does_not_call_provider_again(pg_database) -> None:
    class CountingForecast(SafeMockForecastProvider):
        calls = 0

        def predict(self, history, horizon):
            self.calls += 1
            return super().predict(history, horizon)

    provider = CountingForecast()
    worker = executor(pg_database, forecast=provider)
    rid = worker.submit(WorkflowRequest(site_id="x", history=[1, 2], horizon=2))
    worker.execute(rid)
    assert worker.execute(rid).status == "demo_only"
    assert provider.calls == 1


def test_provider_return_before_commit_is_retried_after_process_crash(
    pg_database, tmp_path
) -> None:
    calls_file = tmp_path / "forecast-calls.txt"
    rid = executor(pg_database).submit(
        WorkflowRequest(site_id="crash-example", history=[2, 4], horizon=2)
    )
    executor(pg_database).execute(rid, max_stages=1)

    crash_script = """
import os
from gridcast_public.adapters import (
    DisabledDecisionProvider, FiniteValueQualityProvider, SafeMockForecastProvider
)
from gridcast_public.durable_store import DurableStore
from gridcast_public.executor import DurableExecutor
class CountingForecast(SafeMockForecastProvider):
    def predict(self, history, horizon):
        with open(os.environ['CALLS_FILE'], 'a', encoding='utf-8') as f: f.write('call\\n')
        return super().predict(history, horizon)
class CrashBeforeCommit(DurableStore):
    def commit_stage(self, *args, **kwargs):
        if kwargs.get('next_stage') == 'review': os._exit(42)
        return super().commit_stage(*args, **kwargs)
store = CrashBeforeCommit(
    os.environ['TEST_DSN'], bytes(range(32)), lease_seconds=0.1,
    schema=os.environ['TEST_SCHEMA']
)
worker = DurableExecutor(
    store, FiniteValueQualityProvider(), CountingForecast(), DisabledDecisionProvider()
)
worker.execute(os.environ['TEST_REQUEST'])
"""
    env = child_env(pg_database, rid)
    env["CALLS_FILE"] = str(calls_file)
    crashed = subprocess.run([sys.executable, "-c", crash_script], env=env, check=False)
    assert crashed.returncode == 42
    time.sleep(0.15)
    assert executor(pg_database).store.status(rid).next_stage == "forecast"

    resume_script = """
import os
from gridcast_public.adapters import (
    DisabledDecisionProvider, FiniteValueQualityProvider, SafeMockForecastProvider
)
from gridcast_public.durable_store import DurableStore
from gridcast_public.executor import DurableExecutor
class CountingForecast(SafeMockForecastProvider):
    def predict(self, history, horizon):
        with open(os.environ['CALLS_FILE'], 'a', encoding='utf-8') as f: f.write('call\\n')
        return super().predict(history, horizon)
store = DurableStore(
    os.environ['TEST_DSN'], bytes(range(32)), schema=os.environ['TEST_SCHEMA']
)
worker = DurableExecutor(
    store, FiniteValueQualityProvider(), CountingForecast(), DisabledDecisionProvider()
)
assert worker.execute(os.environ['TEST_REQUEST']).status == 'demo_only'
"""
    subprocess.run([sys.executable, "-c", resume_script], env=env, check=True)
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["call", "call"]
