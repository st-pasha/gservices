import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """
    Paces calls so that a per-minute quota is never exceeded.

    Retries answer a *burst*: something momentarily went wrong, so wait and ask
    again. They cannot answer a quota. A caller that steadily asks for more than
    it is allowed gets `429`s no matter how patiently it retries — the backoff
    just spreads the same excess over a longer wall clock, and eventually one
    call runs out of budget and the whole job dies. The only fix is to ask more
    slowly, which is what this does.

    `acquire()` blocks until starting a request would keep the last
    [requests_per_minute] starts inside a rolling window, then records the
    start and returns. A caller that routes every request through it therefore
    runs *at* the quota rather than crashing into it: no `429`s, and no
    idle time beyond what the quota actually forces.

    The window slides rather than resetting on the minute. A fixed window lets
    a caller spend the whole allowance in its last second and the next
    window's in its first, which is twice the limit across that boundary —
    exactly the burst the quota is there to prevent.

    Thread-safe, and shared by every request of one service. Waiting threads
    are not queued fairly: each re-checks after sleeping and one of them wins.
    Under contention that is a scheduling accident, not starvation — every
    waiter is sleeping for the same slot and wakes at the same time.
    """

    def __init__(self, requests_per_minute: int, *, window: float = 60.0):
        """
        Allows [requests_per_minute] starts per [window] seconds.

        [window] exists because Google's quotas are per minute but its
        accounting is neither published nor exactly aligned with ours; shrinking
        the window (or the limit) buys margin. Tests use it to avoid sleeping.
        """
        if requests_per_minute <= 0:
            raise ValueError(
                f"`requests_per_minute` must be positive, got {requests_per_minute}"
            )
        if window <= 0:
            raise ValueError(f"`window` must be positive, got {window}")
        self._limit = requests_per_minute
        self._window = window
        self._starts: deque[float] = deque()
        self._lock = threading.Lock()

        # Seams for tests, mirroring `HttpRequest`'s own `_sleep` / `_rand`:
        # substituting these drives the limiter's timing without real waiting.
        self._now: Callable[[], float] = time.monotonic
        self._sleep: Callable[[float], None] = time.sleep

    @property
    def requests_per_minute(self) -> int:
        """How many starts are allowed per window."""
        return self._limit

    def acquire(self) -> float:
        """
        Blocks until a request may start, and returns the seconds spent waiting.

        Returns `0.0` when the caller is under quota, which is the common case
        — the limiter costs nothing until it is actually needed.
        """
        waited = 0.0
        while True:
            with self._lock:
                now = self._now()
                self._expire(now)
                if len(self._starts) < self._limit:
                    self._starts.append(now)
                    return waited
                # The oldest start leaves the window at this moment, freeing
                # the slot it holds. Computed under the lock, slept outside it.
                delay = self._starts[0] + self._window - now
            self._sleep(delay)
            waited += delay

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def _expire(self, now: float) -> None:
        """Drops the starts that have fallen out of the window."""
        cutoff = now - self._window
        while self._starts and self._starts[0] <= cutoff:
            self._starts.popleft()
