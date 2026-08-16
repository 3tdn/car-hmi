"""Per-seat write locks for Dev Mode.

When a section (client) enters Dev Mode and selects seats, other sections are
blocked from writing any signal that belongs to those seats until
``block_timeout_sec`` elapses or the owning section leaves Dev Mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

SEAT_IDS: tuple[str, ...] = ("fl", "fr", "rl1", "rl2", "rr1")
DEFAULT_BLOCK_TIMEOUT_SEC = 60.0

# Owner used for requests without an X-Client-Id header — they can still lock, but share one slot.
ANONYMOUS_OWNER = "__anonymous__"


@dataclass(frozen=True)
class SeatLock:
    seat: str
    owner: str
    acquired_at: float
    expires_at: float

    def remaining_sec(self, now: float | None = None) -> float:
        return max(0.0, self.expires_at - (now if now is not None else time.time()))


def seat_of_signal(signal_name: str) -> str | None:
    """Derive the seat id from a signal name (``ACR_FL_RetractRequest`` → ``fl``)."""
    for part in signal_name.split("_"):
        candidate = part.lower()
        if candidate in SEAT_IDS:
            return candidate
    return None


class SeatLockRegistry:
    """In-memory registry holding Dev Mode locks per seat."""

    def __init__(self, default_timeout_sec: float = DEFAULT_BLOCK_TIMEOUT_SEC) -> None:
        self.default_timeout_sec = float(default_timeout_sec)
        self._locks: dict[str, SeatLock] = {}

    # ── Queries ─────────────────────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        expired = [seat for seat, lock in self._locks.items() if lock.expires_at <= now]
        for seat in expired:
            self._locks.pop(seat, None)

    def active_locks(self, now: float | None = None) -> dict[str, SeatLock]:
        timestamp = now if now is not None else time.time()
        self._prune(timestamp)
        return dict(self._locks)

    def lock_for_seat(self, seat: str, now: float | None = None) -> SeatLock | None:
        return self.active_locks(now).get(seat.lower())

    def blocking_lock(
        self, signal_name: str, owner: str | None, now: float | None = None
    ) -> SeatLock | None:
        """Return the lock preventing ``owner`` from writing ``signal_name``, or None if allowed."""
        seat = seat_of_signal(signal_name)
        if seat is None:
            return None
        lock = self.lock_for_seat(seat, now)
        if lock is None:
            return None
        if lock.owner == (owner or ANONYMOUS_OWNER):
            return None
        return lock

    # ── Mutations ─────────────────────────────────────────────────────────

    def acquire(
        self,
        seat: str,
        owner: str | None,
        timeout_sec: float | None = None,
        now: float | None = None,
    ) -> SeatLock:
        """Take or renew the lock for a seat.

        Raises ``PermissionError`` if another section holds it.
        """
        timestamp = now if now is not None else time.time()
        seat_id = seat.lower()
        owner_id = owner or ANONYMOUS_OWNER
        existing = self.lock_for_seat(seat_id, timestamp)
        if existing is not None and existing.owner != owner_id:
            raise PermissionError(existing.owner)

        duration = float(timeout_sec) if timeout_sec else self.default_timeout_sec
        lock = SeatLock(
            seat=seat_id,
            owner=owner_id,
            acquired_at=existing.acquired_at if existing else timestamp,
            expires_at=timestamp + duration,
        )
        self._locks[seat_id] = lock
        return lock

    def release(self, seat: str, owner: str | None) -> bool:
        seat_id = seat.lower()
        lock = self._locks.get(seat_id)
        if lock is None:
            return False
        if lock.owner != (owner or ANONYMOUS_OWNER):
            return False
        self._locks.pop(seat_id, None)
        return True

    def release_owner(self, owner: str | None) -> list[str]:
        """Release every lock held by a section — used when leaving Dev Mode."""
        owner_id = owner or ANONYMOUS_OWNER
        released = [seat for seat, lock in self._locks.items() if lock.owner == owner_id]
        for seat in released:
            self._locks.pop(seat, None)
        return released


_registry: SeatLockRegistry | None = None


def get_seat_lock_registry(default_timeout_sec: float | None = None) -> SeatLockRegistry:
    """Registry shared by the whole process."""
    global _registry
    if _registry is None:
        _registry = SeatLockRegistry(
            default_timeout_sec if default_timeout_sec is not None else DEFAULT_BLOCK_TIMEOUT_SEC
        )
    elif default_timeout_sec is not None:
        _registry.default_timeout_sec = float(default_timeout_sec)
    return _registry


def reset_seat_lock_registry() -> None:
    """Test-only helper."""
    global _registry
    _registry = None
