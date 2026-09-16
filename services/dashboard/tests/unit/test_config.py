"""Configuration must fail fast and keep the database URL out of logs (GR-4, GR-5)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from finstream_dashboard.config import Settings


def test_defaults(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.dashboard_cache_ttl_seconds == 60
    assert settings.dashboard_default_interval == "1d"
    assert settings.dashboard_max_rows == 5000


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_database_url_is_not_printed(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(database_url="postgresql+psycopg://u:sup3rsecret@db:5432/x")
    assert "sup3rsecret" not in repr(settings)
    assert "sup3rsecret" not in str(settings)


@pytest.mark.parametrize(
    ("field", "value"), [("dashboard_cache_ttl_seconds", -1), ("dashboard_max_rows", 10)]
)
def test_out_of_range_values_are_rejected(
    make_settings: Callable[..., Settings], field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})
