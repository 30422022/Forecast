from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from gridcast_public.contracts import AgentMessage, DurableStatus
from gridcast_public.crypto import decrypt, encrypt


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect_postgres(dsn: str, schema: str = "public") -> psycopg.Connection:
    """Open a short-lived transaction in one validated PostgreSQL schema."""
    if not dsn:
        raise ValueError("PostgreSQL connection is required")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid PostgreSQL schema")
    return psycopg.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=5,
        options=f"-c search_path={schema}",
    )


class DurableStore:
    """PostgreSQL journal with encrypted checkpoints and fenced stage commits."""

    def __init__(
        self, dsn: str, key: bytes, lease_seconds: float = 30.0, *, schema: str = "public"
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.dsn = dsn
        self.schema = schema
        self.key = key
        self.lease_seconds = lease_seconds
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                  request_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  next_stage TEXT,
                  attempt INTEGER NOT NULL,
                  error_code TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  cp_version INTEGER NOT NULL,
                  nonce BYTEA NOT NULL,
                  ciphertext BYTEA NOT NULL,
                  lease_owner TEXT,
                  lease_until DOUBLE PRECISION,
                  fencing BIGINT NOT NULL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                  message_order BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                  request_id TEXT NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE,
                  stage TEXT NOT NULL,
                  attempt INTEGER NOT NULL,
                  kind TEXT NOT NULL,
                  message_json TEXT NOT NULL,
                  UNIQUE (request_id, stage, attempt, kind)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS idempotency (
                  key_hash TEXT PRIMARY KEY,
                  request_hash TEXT NOT NULL,
                  request_id TEXT NOT NULL REFERENCES runs(request_id) ON DELETE CASCADE
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS runs_terminal_age "
                "ON runs (updated_at DESC, request_id) WHERE status IN ('succeeded', 'failed')"
            )

    def connect(self) -> psycopg.Connection:
        return connect_postgres(self.dsn, self.schema)

    def create(self, request_id: str, state: dict[str, Any]) -> None:
        version = 1
        nonce, ciphertext = encrypt(self.key, request_id, version, _encode(state))
        stamp = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs (request_id, status, next_stage, attempt, error_code, "
                "created_at, updated_at, cp_version, nonce, ciphertext, lease_owner, "
                "lease_until, fencing) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    request_id,
                    "queued",
                    "quality",
                    0,
                    None,
                    stamp,
                    stamp,
                    version,
                    nonce,
                    ciphertext,
                    None,
                    None,
                    0,
                ),
            )

    def claim(self, request_id: str, owner: str) -> int | None:
        moment = time.time()
        with self.connect() as db:
            row = db.execute(
                "UPDATE runs SET status='running', lease_owner=%s, lease_until=%s, "
                "fencing=fencing+1, updated_at=%s WHERE request_id=%s "
                "AND status NOT IN ('succeeded','failed') "
                "AND (lease_until IS NULL OR lease_until<=%s) RETURNING fencing",
                (owner, moment + self.lease_seconds, now(), request_id, moment),
            ).fetchone()
            return row["fencing"] if row else None

    def renew(self, request_id: str, owner: str, fence: int) -> bool:
        moment = time.time()
        with self.connect() as db:
            result = db.execute(
                "UPDATE runs SET lease_until=%s, updated_at=%s WHERE request_id=%s "
                "AND lease_owner=%s AND fencing=%s AND lease_until>%s AND status='running'",
                (moment + self.lease_seconds, now(), request_id, owner, fence, moment),
            )
            return result.rowcount == 1

    def read(self, request_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE request_id=%s", (request_id,)
            ).fetchone()
        if row is None:
            return None
        state = json.loads(
            decrypt(
                self.key,
                request_id,
                row["cp_version"],
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
            )
        )
        return state, row

    def commit_stage(
        self,
        request_id: str,
        owner: str,
        fence: int,
        *,
        state: dict[str, Any],
        next_stage: str | None,
        status: str,
        attempt: int,
        error_code: str | None,
        messages: list[AgentMessage],
    ) -> bool:
        current = self.read(request_id)
        if current is None:
            return False
        previous_version = current[1]["cp_version"]
        version = previous_version + 1
        nonce, ciphertext = encrypt(self.key, request_id, version, _encode(state))
        moment = time.time()
        terminal = status in {"succeeded", "failed"}
        with self.connect() as db:
            updated = db.execute(
                "UPDATE runs SET status=%s, next_stage=%s, attempt=%s, error_code=%s, "
                "updated_at=%s, cp_version=%s, nonce=%s, ciphertext=%s, lease_owner=%s, "
                "lease_until=%s WHERE request_id=%s AND lease_owner=%s AND fencing=%s "
                "AND lease_until>%s AND status='running' AND cp_version=%s "
                "RETURNING request_id",
                (
                    status,
                    next_stage,
                    attempt,
                    error_code,
                    now(),
                    version,
                    nonce,
                    ciphertext,
                    None if terminal else owner,
                    None if terminal else moment + self.lease_seconds,
                    request_id,
                    owner,
                    fence,
                    moment,
                    previous_version,
                ),
            ).fetchone()
            if updated is None:
                return False
            for message in messages:
                db.execute(
                    "INSERT INTO messages (request_id, stage, attempt, kind, message_json) "
                    "VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (request_id, stage, attempt, kind) DO NOTHING",
                    (
                        request_id,
                        message.recipient,
                        message.attempt,
                        message.kind,
                        message.model_dump_json(),
                    ),
                )
        return True

    def release(self, request_id: str, owner: str, fence: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET status='recoverable', lease_owner=NULL, lease_until=NULL, "
                "updated_at=%s WHERE request_id=%s AND lease_owner=%s AND fencing=%s "
                "AND status='running'",
                (now(), request_id, owner, fence),
            )

    def status(self, request_id: str) -> DurableStatus | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_id=%s", (request_id,)).fetchone()
            if row is None:
                return None
            messages = db.execute(
                "SELECT message_json FROM messages WHERE request_id=%s ORDER BY message_order",
                (request_id,),
            ).fetchall()
        status = row["status"]
        if status == "running" and row["lease_until"] and row["lease_until"] <= time.time():
            status = "recoverable"
        return DurableStatus(
            request_id=request_id,
            status=status,
            next_stage=row["next_stage"],
            attempt=row["attempt"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            messages=[AgentMessage.model_validate_json(item["message_json"]) for item in messages],
        )

    def idempotent_create(
        self, key_hash: str, request_hash: str, request_id: str, state: dict[str, Any]
    ) -> tuple[str, bool]:
        version = 1
        nonce, ciphertext = encrypt(self.key, request_id, version, _encode(state))
        stamp = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs (request_id, status, next_stage, attempt, error_code, "
                "created_at, updated_at, cp_version, nonce, ciphertext, lease_owner, "
                "lease_until, fencing) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    request_id,
                    "queued",
                    "quality",
                    0,
                    None,
                    stamp,
                    stamp,
                    version,
                    nonce,
                    ciphertext,
                    None,
                    None,
                    0,
                ),
            )
            inserted = db.execute(
                "INSERT INTO idempotency (key_hash, request_hash, request_id) "
                "VALUES (%s,%s,%s) ON CONFLICT (key_hash) DO NOTHING RETURNING request_id",
                (key_hash, request_hash, request_id),
            ).fetchone()
            if inserted is not None:
                return request_id, True
            db.rollback()  # Discard the new run if another request already owns this key.
            prior = db.execute(
                "SELECT request_hash, request_id FROM idempotency WHERE key_hash=%s", (key_hash,)
            ).fetchone()
            if prior is None:
                raise psycopg.OperationalError("idempotency lookup unavailable")
            if prior["request_hash"] != request_hash:
                raise ValueError("idempotency conflict")
            return prior["request_id"], False


def _encode(state: dict[str, Any]) -> bytes:
    return json.dumps(state, separators=(",", ":"), allow_nan=False).encode()
