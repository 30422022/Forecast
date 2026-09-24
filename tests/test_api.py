import base64
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from gridcast_public.adapters import (
    DisabledDecisionProvider,
    FiniteValueQualityProvider,
)
from gridcast_public.api import app
from gridcast_public.contracts import WorkflowFailureResponse, WorkflowReceipt
from gridcast_public.receipts import ReceiptStore
from gridcast_public.workflow import build_workflow


class FailingForecast:
    name = "forecast_test"

    def predict(self, history: list[float], horizon: int) -> list[float]:
        del history, horizon
        raise RuntimeError("SENSITIVE provider failure details")


class NonFiniteForecast:
    name = "forecast_test"

    def predict(self, history: list[float], horizon: int) -> list[float]:
        del history, horizon
        return [float("nan"), float("inf")]


def test_public_api_success_receipt_and_privacy_shape() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/workflows/run",
            json={"site_id": "private-site", "history": [1, 2, 3], "horizon": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "demo_only"
        receipt_response = client.get(f"/v1/workflows/receipts/{body['request_id']}")
        assert receipt_response.status_code == 200
        receipt = receipt_response.json()
        assert set(receipt) == {
            "request_id",
            "status",
            "last_node",
            "error_code",
            "created_at",
            "expires_at",
        }
        assert receipt["status"] == "succeeded"
        assert receipt["last_node"] == "audit"
        assert "private-site" not in receipt_response.text
        assert "forecast" not in receipt_response.text


def test_provider_failure_api_returns_structured_502_and_receipt() -> None:
    with TestClient(app) as client:
        app.state.graph = build_workflow(
            FiniteValueQualityProvider(), FailingForecast(), DisabledDecisionProvider()
        )
        response = client.post(
            "/v1/workflows/run",
            json={"site_id": "secret-site", "history": [1, 2, 3], "horizon": 2},
        )
        assert response.status_code == 502
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["error_code"] == "FORECAST_PROVIDER_ERROR"
        assert payload["failed_node"] == "forecast"
        assert "forecast" not in payload
        assert "decision" not in payload
        assert "SENSITIVE" not in response.text
        assert "secret-site" not in response.text
        receipt = client.get(f"/v1/workflows/receipts/{payload['request_id']}")
        assert receipt.status_code == 200
        assert receipt.json()["status"] == "failed"
        assert receipt.json()["error_code"] == "FORECAST_PROVIDER_ERROR"


def test_nonfinite_forecast_api_returns_502_instead_of_serialization_500() -> None:
    with TestClient(app) as client:
        app.state.graph = build_workflow(
            FiniteValueQualityProvider(), NonFiniteForecast(), DisabledDecisionProvider()
        )
        response = client.post(
            "/v1/workflows/run",
            json={"site_id": "example", "history": [1, 2, 3], "horizon": 2},
        )
        assert response.status_code == 502
        payload = response.json()
        assert payload["error_code"] == "FORECAST_OUTPUT_INVALID"
        assert payload["failed_node"] == "forecast"
        assert "NaN" not in response.text
        assert "Infinity" not in response.text


def test_invalid_request_does_not_create_receipt() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/workflows/run", json={"site_id": "", "history": [1], "horizon": 0}
        )
        assert response.status_code == 422


def test_receipt_endpoint_returns_404_for_unknown_id() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/workflows/receipts/unknown").status_code == 404


def test_receipt_store_expiry_and_capacity() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = ReceiptStore(capacity=2, ttl=timedelta(minutes=10), clock=lambda: now[0])

    def receipt(request_id: str) -> WorkflowFailureResponse:
        return WorkflowFailureResponse(
            request_id=request_id,
            error_code="FORECAST_PROVIDER_ERROR",
            failed_node="forecast",
            trace=[
                {"node": "forecast", "status": "failed", "detail": "provider_error"},
                {"node": "audit", "status": "succeeded", "detail": "execution_metadata_sha256"},
            ],
            provenance={"fingerprint": "a" * 64},
        )

    for key in ("first", "second", "third"):
        store.record(receipt(key))
    assert store.get("first") is None
    assert store.get("second") is not None
    assert store.get("third") is not None
    stored = store.get("third")
    assert isinstance(stored, WorkflowReceipt)
    assert set(stored.model_dump()) == {
        "request_id",
        "status",
        "last_node",
        "error_code",
        "created_at",
        "expires_at",
    }
    now[0] += timedelta(minutes=10, seconds=1)
    assert store.get("second") is None
    assert store.get("third") is None


def test_durable_api_reports_mode_and_keeps_legacy_receipt_shape(
    pg_database, monkeypatch
) -> None:
    dsn, schema = pg_database
    monkeypatch.setenv("GRIDCAST_CHECKPOINT_KEY", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setenv("GRIDCAST_DATABASE_URL", dsn)
    monkeypatch.setenv("GRIDCAST_DB_SCHEMA", schema)
    with TestClient(app) as client:
        assert client.get("/health").json()["recovery_available"] == "true"
        assert client.get("/v1/capabilities").json()["storage"] == "postgresql_aes_gcm"
        response = client.post(
            "/v1/workflows/run?durable=true",
            json={"site_id": "hidden-site", "history": [10, 20], "horizon": 2},
        )
        assert response.status_code == 200
        rid = response.json()["request_id"]
        status = client.get(f"/v1/workflows/{rid}").json()
        assert status["status"] == "succeeded"
        assert {item["sender"] for item in status["messages"]} >= {
            "quality",
            "forecast",
            "review",
            "decision",
            "audit",
        }
        receipt = client.get(f"/v1/workflows/receipts/{rid}").json()
        assert receipt["expires_at"] is None
        assert "hidden-site" not in str(status)
        repeated = client.post(f"/v1/workflows/{rid}/resume")
        assert repeated.status_code == 200


def test_durable_mode_without_key_is_explicitly_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("GRIDCAST_CHECKPOINT_KEY", raising=False)
    monkeypatch.delenv("GRIDCAST_DATABASE_URL", raising=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["recovery_available"] == "false"
        response = client.post(
            "/v1/workflows/run?durable=true",
            json={"site_id": "site", "history": [1, 2], "horizon": 1},
        )
        assert response.status_code == 503


def test_durable_mode_without_postgres_url_is_explicitly_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("GRIDCAST_CHECKPOINT_KEY", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.delenv("GRIDCAST_DATABASE_URL", raising=False)
    with TestClient(app) as client:
        assert client.get("/v1/capabilities").json()["storage"] == "unavailable"
        response = client.post(
            "/v1/workflows/run?durable=true",
            json={"site_id": "site", "history": [1, 2], "horizon": 1},
        )
        assert response.status_code == 503


def test_durable_idempotency_key_deduplicates_and_rejects_different_body(
    pg_database, monkeypatch
) -> None:
    dsn, schema = pg_database
    monkeypatch.setenv("GRIDCAST_CHECKPOINT_KEY", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.delenv("GRIDCAST_IDEMPOTENCY_KEY", raising=False)
    monkeypatch.setenv("GRIDCAST_DATABASE_URL", dsn)
    monkeypatch.setenv("GRIDCAST_DB_SCHEMA", schema)
    payload = {"site_id": "safe-example", "history": [1, 2], "horizon": 2}
    with TestClient(app) as client:
        first = client.post(
            "/v1/workflows/run?durable=true", headers={"Idempotency-Key": "sample-1"}, json=payload
        )
        second = client.post(
            "/v1/workflows/run?durable=true", headers={"Idempotency-Key": "sample-1"}, json=payload
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["request_id"] == second.json()["request_id"]
        changed = {**payload, "horizon": 3}
        conflict = client.post(
            "/v1/workflows/run?durable=true", headers={"Idempotency-Key": "sample-1"}, json=changed
        )
        assert conflict.status_code == 409
