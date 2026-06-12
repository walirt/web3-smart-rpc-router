"""Pydantic v2 schema for the Web3 Smart RPC Router configuration contract.

This module is the single source of truth for what a valid router
configuration looks like. Any YAML loaded via :mod:`core.config` is
parsed against these models, and any deviation from the constraints
defined here will raise a :class:`pydantic.ValidationError`.

Phase 1 deliberately rejects (rather than silently coerces) every
ambiguity: unknown fields, missing required values, out-of-range
numbers, non-HTTP(S) URL schemes, duplicate provider names, duplicate
priorities, and so on.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class RoutingStrategy(str, Enum):
    """The four routing strategies locked in for Phase 1.

    Stored on disk in lower_snake_case form. New strategies must be
    added here (and to the plan) before they can be referenced from
    YAML.
    """

    ROUND_ROBIN = "round_robin"
    PRIORITY = "priority"
    LOWEST_LATENCY = "lowest_latency"
    FAILOVER = "failover"


def _validate_http_url(value: str) -> str:
    """Ensure the URL uses an http or https scheme."""
    parsed = urlparse(value)
    if not parsed.scheme:
        raise ValueError("url must include a scheme (http or https)")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"url scheme must be 'http' or 'https', got '{parsed.scheme}'"
        )
    return value


# A plain str that has been verified to use http or https.
HttpUrlStr = Annotated[str, AfterValidator(_validate_http_url)]


class GlobalSettings(BaseModel):
    """Process-wide router settings."""

    model_config = ConfigDict(extra="forbid")

    listen_port: int = Field(..., ge=1, le=65535)
    probe_interval_seconds: float = Field(..., gt=0)
    request_timeout_seconds: float = Field(..., gt=0)
    routing_strategy: RoutingStrategy
    max_retries: int = Field(3, ge=1)


class RpcNode(BaseModel):
    """Configuration for a single upstream RPC endpoint."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1)
    url: HttpUrlStr
    priority: int = Field(..., ge=1)
    weight: int = Field(1, ge=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider", mode="before")
    @classmethod
    def _strip_provider(cls, value: object) -> object:
        """Trim surrounding whitespace from the provider label."""
        if isinstance(value, str):
            return value.strip()
        return value


class RouterConfig(BaseModel):
    """The top-level router configuration object."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    global_: GlobalSettings = Field(..., alias="global")
    rpc_nodes: list[RpcNode] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_unique_provider_and_priority(self) -> RouterConfig:
        """Enforce uniqueness of provider labels and priorities across nodes."""
        for field_name in ("provider", "priority"):
            values = [getattr(node, field_name) for node in self.rpc_nodes]
            if len(set(values)) != len(values):
                raise ValueError(
                    f"{field_name} values must be unique across rpc_nodes"
                )
        return self


__all__ = [
    "GlobalSettings",
    "HttpUrlStr",
    "RpcNode",
    "RouterConfig",
    "RoutingStrategy",
]
