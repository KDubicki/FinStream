"""Docker HEALTHCHECK: is the scheduler still alive?

The scheduler touches a heartbeat file on a short interval. If that file goes stale the process
is wedged even though the container still looks "up", which is exactly the failure a healthcheck
should catch. Exits 0 when the heartbeat is fresh, 1 otherwise.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from finstream_ingestor.config import Settings

#: How many heartbeat intervals may be missed before the service counts as unhealthy.
STALE_AFTER_INTERVALS = 3


def heartbeat_age(path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the heartbeat file was touched, or None when it does not exist yet."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return (time.time() if now is None else now) - modified


def is_healthy(path: Path, *, heartbeat_every_seconds: int, now: float | None = None) -> bool:
    age = heartbeat_age(path, now=now)
    if age is None:
        return False
    return age <= heartbeat_every_seconds * STALE_AFTER_INTERVALS


def main() -> int:
    settings = Settings()  # type: ignore[call-arg]  # values come from the environment
    healthy = is_healthy(
        settings.heartbeat_file,
        heartbeat_every_seconds=settings.heartbeat_every_seconds,
    )
    if not healthy:
        age = heartbeat_age(settings.heartbeat_file)
        detail = "missing" if age is None else f"{age:.0f}s old"
        print(f"heartbeat {settings.heartbeat_file} is {detail}", file=sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
