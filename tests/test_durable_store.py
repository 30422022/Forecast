from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from gridcast_public.durable_store import DurableStore
from scripts.prune_runtime import prune


def test_prune_removes_old_and_excess_terminal_tasks_but_keeps_recoverable(pg_database) -> None:
    dsn, schema = pg_database
    store = DurableStore(dsn, bytes(range(32)), schema=schema)
    state = {
        "request": {"site_id": "x", "history": [1, 2], "horizon": 1},
        "trace": [],
        "messages": [],
        "attempt": 0,
    }
    ids = [str(uuid.uuid4()) for _ in range(4)]
    for request_id in ids:
        store.create(request_id, state)
    old = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    recent = datetime.now(UTC).isoformat()
    with store.connect() as db:
        for request_id, status, stamp in (
            (ids[0], "succeeded", old),
            (ids[1], "succeeded", recent),
            (ids[2], "failed", recent),
            (ids[3], "recoverable", old),
        ):
            db.execute(
                "UPDATE runs SET status=%s, updated_at=%s WHERE request_id=%s",
                (status, stamp, request_id),
            )
    assert prune(dsn, schema=schema, retention_days=30, max_terminal_runs=1) == 2
    with store.connect() as db:
        remaining = {
            row["request_id"]: row["status"]
            for row in db.execute("SELECT request_id, status FROM runs")
        }
    assert len(remaining) == 2
    assert ids[0] not in remaining
    assert remaining[ids[3]] == "recoverable"
    assert len({ids[1], ids[2]} & remaining.keys()) == 1


def test_concurrent_idempotency_key_creates_one_run(pg_database) -> None:
    dsn, schema = pg_database
    store = DurableStore(dsn, bytes(range(32)), schema=schema)
    state = {
        "request": {"site_id": "demo", "history": [1, 2], "horizon": 1},
        "trace": [],
        "messages": [],
        "attempt": 0,
    }

    def submit():
        return store.idempotent_create("same-key", "same-body", str(uuid.uuid4()), state)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) AS count FROM runs").fetchone()["count"]
    assert count == 1
