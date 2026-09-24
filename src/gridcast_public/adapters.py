from __future__ import annotations

from typing import Any


class FiniteValueQualityProvider:
    """Public boundary check without private cleaning or feature logic."""

    name = "finite_value_guard"

    def validate(self, history: list[float]) -> tuple[list[float], list[str]]:
        if len(history) < 2:
            raise ValueError("at least two history values are required")
        return list(history), []


class SafeMockForecastProvider:
    """Non-production stub used only to prove dependency injection works."""

    name = "safe_mock_last_value"

    def predict(self, history: list[float], horizon: int) -> list[float]:
        return [float(history[-1])] * horizon


class DisabledDecisionProvider:
    """Explicitly prevents optimization or operational control in the public preview."""

    name = "disabled"
    enabled = False

    def advise(self, forecast: list[float]) -> dict[str, Any]:
        del forecast
        return {
            "status": "adapter_not_configured",
            "message": "Private decision provider is not included in the public preview.",
            "actionable": False,
        }
