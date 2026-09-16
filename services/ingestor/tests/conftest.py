"""Shared fixtures.

Automated tests never reach the network (AGENTS.md GR-7): the autouse guard below fails any
test that opens a socket to a non-local host. Integration tests talk to a container on
localhost, which stays allowed.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from finstream_ingestor.config import Settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 - comparison only

TEST_DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/test"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if it tries to reach a non-local host."""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in LOCAL_HOSTS:
            raise AssertionError(
                f"network access to {host!r} is forbidden in tests (GR-7); mock the source instead"
            )
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def make_settings() -> Iterator[Callable[..., Settings]]:
    """Build Settings without reading a developer's real .env file."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"database_url": TEST_DATABASE_URL, **overrides}
        return Settings(_env_file=None, **values)

    yield factory
