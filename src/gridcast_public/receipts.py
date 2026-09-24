from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import RLock

from gridcast_public.contracts import WorkflowFailureResponse, WorkflowReceipt, WorkflowResponse


class ReceiptStore:
    """Bounded, process-local execution metadata with a short retention window."""

    def __init__(
        self,
        capacity: int = 100,
        ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if capacity < 1 or ttl.total_seconds() <= 0:
            raise ValueError("capacity and ttl must be positive")
        self.capacity = capacity
        self.ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._items: OrderedDict[str, WorkflowReceipt] = OrderedDict()
        self._lock = RLock()

    def _purge_expired(self, now: datetime) -> None:
        expired = [key for key, value in self._items.items() if _parse(value.expires_at) <= now]
        for key in expired:
            self._items.pop(key, None)

    def record(self, response: WorkflowResponse | WorkflowFailureResponse) -> WorkflowReceipt:
        now = self._clock().astimezone(UTC)
        created_at = now.isoformat()
        expires_at = (now + self.ttl).isoformat()
        receipt = WorkflowReceipt(
            request_id=response.request_id,
            status="failed" if isinstance(response, WorkflowFailureResponse) else "succeeded",
            last_node=response.trace[-1].node,
            error_code=(
                response.error_code if isinstance(response, WorkflowFailureResponse) else None
            ),
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._lock:
            self._purge_expired(now)
            self._items.pop(receipt.request_id, None)
            self._items[receipt.request_id] = receipt
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return receipt.model_copy(deep=True)

    def get(self, request_id: str) -> WorkflowReceipt | None:
        now = self._clock().astimezone(UTC)
        with self._lock:
            self._purge_expired(now)
            receipt = self._items.get(request_id)
            return receipt.model_copy(deep=True) if receipt else None


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
