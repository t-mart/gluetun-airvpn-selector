from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    gluetun_base_url: str
    gluetun_api_key: str
    airvpn_status_url: str
    airvpn_cache_seconds: float
    airvpn_timeout_seconds: float
    gluetun_timeout_seconds: float
    host: str
    port: int

    @classmethod
    def from_env(cls) -> Config:
        config = cls(
            gluetun_base_url=os.getenv(
                "GLUETUN_BASE_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            gluetun_api_key=os.getenv("GLUETUN_API_KEY", ""),
            airvpn_status_url=os.getenv(
                "AIRVPN_STATUS_URL", "https://airvpn.org/api/status/"
            ),
            airvpn_cache_seconds=_positive_float("AIRVPN_CACHE_SECONDS", 300),
            airvpn_timeout_seconds=_positive_float("AIRVPN_TIMEOUT_SECONDS", 15),
            gluetun_timeout_seconds=_positive_float("GLUETUN_TIMEOUT_SECONDS", 10),
            host=os.getenv("HOST", "127.0.0.1"),
            port=_port("PORT", 8081),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.gluetun_api_key:
            raise ValueError("Set GLUETUN_API_KEY for API key authentication.")


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number.") from error
    if value <= 0:
        raise ValueError(f"{name} must be more than zero.")
    return value


def _port(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be from 1 through 65535.")
    return value
