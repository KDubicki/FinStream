"""Startup wiring: the order matters, and shutdown must always release the engine."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from finstream_ingestor import main as main_module


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class FakeScheduler:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_calls: list[bool] = []

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace every external touchpoint and record the order they are used in."""
    calls: list[str] = []
    engine = FakeEngine()
    scheduler = FakeScheduler()

    monkeypatch.setattr(
        main_module, "create_db_engine", lambda _url: (calls.append("engine"), engine)[1]
    )
    monkeypatch.setattr(
        main_module, "wait_for_db", lambda *_args, **_kwargs: calls.append("wait_for_db")
    )
    monkeypatch.setattr(
        main_module, "init_schema", lambda *_args, **_kwargs: calls.append("init_schema")
    )
    monkeypatch.setattr(
        main_module, "YahooSource", lambda **_kwargs: calls.append("source") or object()
    )
    monkeypatch.setattr(
        main_module,
        "build_scheduler",
        lambda *_args, **_kwargs: (calls.append("scheduler"), scheduler)[1],
    )
    monkeypatch.setattr(
        main_module, "install_signal_handlers", lambda _scheduler: calls.append("signals")
    )
    return {"calls": calls, "engine": engine, "scheduler": scheduler}


def test_startup_order(wiring: dict[str, Any], make_settings) -> None:
    """The database must be reachable and migrated before any job can fire."""
    assert main_module.run(make_settings()) == 0

    calls = wiring["calls"]
    assert calls.index("wait_for_db") < calls.index("init_schema") < calls.index("scheduler")
    assert wiring["scheduler"].started is True


def test_engine_is_released_even_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch, make_settings
) -> None:
    """A database that never comes back must not leak the pool on the way out."""
    engine = FakeEngine()
    monkeypatch.setattr(main_module, "create_db_engine", lambda _url: engine)

    def unreachable(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(main_module, "wait_for_db", unreachable)

    with pytest.raises(RuntimeError, match="database unreachable"):
        main_module.run(make_settings())
    assert engine.disposed is True


def test_interrupt_is_a_clean_stop(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch, make_settings
) -> None:
    scheduler = wiring["scheduler"]

    def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(scheduler, "start", interrupted)

    assert main_module.run(make_settings()) == 0
    assert wiring["engine"].disposed is True


def test_sigterm_shuts_the_scheduler_down_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker compose down` sends SIGTERM; it must not be a kill."""
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))

    scheduler = FakeScheduler()
    main_module.install_signal_handlers(scheduler)  # type: ignore[arg-type]

    assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
    handlers[signal.SIGTERM](int(signal.SIGTERM), None)
    assert scheduler.shutdown_calls == [False], "in-flight work is atomic; do not block shutdown"


def test_main_builds_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, make_settings
) -> None:
    settings = make_settings()
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "run", lambda given: 0 if given is settings else 1)
    assert main_module.main() == 0
