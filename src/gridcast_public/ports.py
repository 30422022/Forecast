from __future__ import annotations

from typing import Any, Protocol


class QualityProvider(Protocol):
    name: str

    def validate(self, history: list[float]) -> tuple[list[float], list[str]]: ...


class ForecastProvider(Protocol):
    name: str

    def predict(self, history: list[float], horizon: int) -> list[float]: ...


class DecisionProvider(Protocol):
    name: str
    enabled: bool

    def advise(self, forecast: list[float]) -> dict[str, Any]: ...
