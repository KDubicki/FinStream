"""Shared fixtures. Automated tests never reach the network (AGENTS.md GR-7)."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from finstream_dashboard.config import Settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 - comparison only
TEST_DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/test"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        # Only TCP addresses are checked; a plain string is an AF_UNIX path, such as the Docker
        # socket testcontainers uses.
        if isinstance(address, tuple):
            host = address[0]
            if isinstance(host, str) and host not in LOCAL_HOSTS:
                raise AssertionError(f"network access to {host!r} is forbidden in tests (GR-7)")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def make_settings() -> Iterator[Callable[..., Settings]]:
    def factory(**overrides: Any) -> Settings:
        return Settings(_env_file=None, **{"database_url": TEST_DATABASE_URL, **overrides})

    yield factory
