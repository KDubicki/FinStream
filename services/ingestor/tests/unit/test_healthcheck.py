"""The healthcheck turns a wedged process into an unhealthy container."""

from __future__ import annotations

from pathlib import Path

from finstream_ingestor.healthcheck import STALE_AFTER_INTERVALS, heartbeat_age, is_healthy


def test_a_missing_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    missing = tmp_path / "never-written"
    assert heartbeat_age(missing) is None
    assert is_healthy(missing, heartbeat_every_seconds=60) is False


def test_a_fresh_heartbeat_is_healthy(tmp_path: Path) -> None:
    beat = tmp_path / "heartbeat"
    beat.touch()
    assert is_healthy(beat, heartbeat_every_seconds=60) is True


def test_a_stale_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    beat = tmp_path / "heartbeat"
    beat.touch()
    stale_by = 60 * STALE_AFTER_INTERVALS + 1
    now = beat.stat().st_mtime + stale_by
    assert is_healthy(beat, heartbeat_every_seconds=60, now=now) is False


def test_the_tolerance_is_several_intervals(tmp_path: Path) -> None:
    """One missed beat must not flap the container; three means it is wedged."""
    beat = tmp_path / "heartbeat"
    beat.touch()
    now = beat.stat().st_mtime + 61
    assert is_healthy(beat, heartbeat_every_seconds=60, now=now) is True


def test_main_reports_healthy_and_unhealthy(tmp_path: Path, monkeypatch) -> None:
    """The exit code is what Docker reads, so it is worth asserting directly."""
    from finstream_ingestor import healthcheck

    beat = tmp_path / "heartbeat"

    class FakeSettings:
        heartbeat_file = beat
        heartbeat_every_seconds = 60

    monkeypatch.setattr(healthcheck, "Settings", FakeSettings)

    assert healthcheck.main() == 1, "no heartbeat yet means unhealthy"

    beat.touch()
    assert healthcheck.main() == 0
