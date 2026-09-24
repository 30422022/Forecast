"""Small original PatchTST-style baseline; not the authors' implementation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import torch
from torch import Tensor, nn
from torch.nn import functional as F


@dataclass(frozen=True)
class PatchTSTConfig:
    input_length: int
    horizon: int
    channels: int
    patch_length: int = 16
    stride: int = 8
    width: int = 64
    heads: int = 4
    depth: int = 2
    dropout: float = 0.1

    def __post_init__(self) -> None:
        if min(
            self.input_length,
            self.horizon,
            self.channels,
            self.patch_length,
            self.stride,
            self.width,
            self.heads,
            self.depth,
        ) <= 0:
            raise ValueError("lengths and model dimensions must be positive")
        if self.patch_length > self.input_length:
            raise ValueError("patch_length cannot exceed input_length")
        if self.width % self.heads:
            raise ValueError("width must be divisible by heads")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be in [0, 1)")


class PatchTSTReference(nn.Module):
    """Channel-independent patch encoder with a shared forecasting head.

    Input: [batch, input_length, channels]. Output: [batch, horizon, channels].
    The instance normalization here has no learnable affine parameters.
    """

    def __init__(self, config: PatchTSTConfig) -> None:
        super().__init__()
        self.config = config
        self.patch_count = ceil((config.input_length - config.patch_length) / config.stride) + 1
        self.right_padding = (
            (self.patch_count - 1) * config.stride
            + config.patch_length
            - config.input_length
        )
        self.patch_projection = nn.Linear(config.patch_length, config.width)
        self.position = nn.Parameter(torch.zeros(1, self.patch_count, config.width))
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.width,
            nhead=config.heads,
            dim_feedforward=config.width * 4,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            encoder_layer,
            num_layers=config.depth,
            enable_nested_tensor=False,
        )
        self.head = nn.Linear(self.patch_count * config.width, config.horizon)

    def forward(self, history: Tensor) -> Tensor:
        if history.ndim != 3 or history.shape[1:] != (
            self.config.input_length,
            self.config.channels,
        ):
            raise ValueError("expected [batch, input_length, channels]")
        batch = history.shape[0]
        center = history.mean(dim=1, keepdim=True)
        scale = (history.var(dim=1, keepdim=True, unbiased=False) + 1e-5).sqrt()
        normalized = ((history - center) / scale).transpose(1, 2)
        if self.right_padding:
            normalized = F.pad(normalized, (0, self.right_padding), mode="replicate")
        patches = normalized.unfold(-1, self.config.patch_length, self.config.stride)
        tokens = self.patch_projection(patches).reshape(
            batch * self.config.channels, self.patch_count, self.config.width
        )
        encoded = self.encoder(tokens + self.position)
        prediction = self.head(encoded.flatten(start_dim=1))
        prediction = prediction.reshape(batch, self.config.channels, self.config.horizon)
        return prediction.transpose(1, 2) * scale + center
