"""Compact time/frequency mixer inspired by FTMixer, not a paper reproduction."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import torch
from torch import Tensor, nn
from torch.nn import functional as F


@dataclass(frozen=True)
class FTMixerConfig:
    input_length: int
    horizon: int
    channels: int
    periods: tuple[int, ...] = (24,)
    frequency_width: int = 64

    def __post_init__(self) -> None:
        if min(self.input_length, self.horizon, self.channels, self.frequency_width) <= 0:
            raise ValueError("lengths and model dimensions must be positive")
        if not self.periods or any(
            period < 2 or period > self.input_length for period in self.periods
        ):
            raise ValueError("periods must be between 2 and input_length")
        if len(set(self.periods)) != len(self.periods):
            raise ValueError("periods must be unique")


class _PeriodMixer(nn.Module):
    def __init__(self, input_length: int, horizon: int, period: int) -> None:
        super().__init__()
        self.period = period
        self.cycles = ceil(input_length / period)
        self.padding = self.cycles * period - input_length
        self.phase = nn.Linear(period, period)
        self.cycle = nn.Linear(self.cycles, self.cycles)
        self.head = nn.Linear(input_length, horizon)

    def forward(self, values: Tensor) -> Tensor:
        # Each channel is reshaped into [cycles, period] for two-axis mixing.
        if self.padding:
            values = F.pad(values, (0, self.padding), mode="replicate")
        grid = values.reshape(*values.shape[:2], self.cycles, self.period)
        grid = grid + F.gelu(self.phase(grid))
        grid = grid + F.gelu(self.cycle(grid.transpose(-1, -2))).transpose(-1, -2)
        return self.head(grid.flatten(start_dim=-2)[..., : self.head.in_features])


class FTMixerReference(nn.Module):
    """Period-grid and real/imaginary FFT branches with learned fusion.

    Input: [batch, input_length, channels]. Output: [batch, horizon, channels].
    This deliberately omits the upstream attention, DCT and Mamba components.
    """

    def __init__(self, config: FTMixerConfig) -> None:
        super().__init__()
        self.config = config
        self.period_branches = nn.ModuleList(
            _PeriodMixer(config.input_length, config.horizon, period)
            for period in config.periods
        )
        frequency_bins = config.input_length // 2 + 1
        self.frequency_head = nn.Sequential(
            nn.Linear(frequency_bins * 2, config.frequency_width),
            nn.GELU(),
            nn.Linear(config.frequency_width, config.horizon),
        )
        self.branch_logits = nn.Parameter(torch.zeros(len(config.periods) + 1))

    def forward(self, history: Tensor) -> Tensor:
        if history.ndim != 3 or history.shape[1:] != (
            self.config.input_length,
            self.config.channels,
        ):
            raise ValueError("expected [batch, input_length, channels]")
        center = history.mean(dim=1, keepdim=True)
        scale = (history.var(dim=1, keepdim=True, unbiased=False) + 1e-5).sqrt()
        normalized = ((history - center) / scale).transpose(1, 2)
        period_outputs = [branch(normalized) for branch in self.period_branches]
        spectrum = torch.fft.rfft(normalized, dim=-1, norm="ortho")
        frequency_features = torch.view_as_real(spectrum).flatten(start_dim=-2)
        outputs = torch.stack([*period_outputs, self.frequency_head(frequency_features)])
        weights = self.branch_logits.softmax(dim=0).view(-1, 1, 1, 1)
        mixed = (outputs * weights).sum(dim=0).transpose(1, 2)
        return mixed * scale + center
