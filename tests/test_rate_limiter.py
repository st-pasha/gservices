"""
Unit tests for `gservices.rate_limiter`.

Most tests drive a `RateLimiter` against a clock the test advances by hand, so
the pacing logic runs for real without anything actually sleeping. The last
group checks the wiring: pacing is dead code unless the services install it.
"""

import threading
import time
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from gservices.drive.drive_service import DriveService
from gservices.gmail.gmail_service import GmailService
from gservices.google_services import GoogleServices
from gservices.rate_limiter import RateLimiter
from gservices.retrying_http_request import RetryingHttpRequest
from gservices.sheets.sheets_service import DEFAULT_REQUESTS_PER_MINUTE, SheetsService


class _FakeClock:
    """A monotonic clock the test moves by hand.

    Wired into the limiter's `_now` / `_sleep` seams, it makes "waiting"
    instant and exactly observable: `sleep()` records the delay and jumps the
    clock forward by it."""

    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _paced(limit: int, window: float = 60.0) -> tuple[RateLimiter, _FakeClock]:
    """A limiter running on a hand-driven clock."""
    limiter = RateLimiter(limit, window=window)
    clock = _FakeClock()
    limiter._now = clock.now
    limiter._sleep = clock.sleep
    return limiter, clock


# ----------------------------------------------------------------------------
# Pacing
# ----------------------------------------------------------------------------


def test_a_caller_under_quota_never_waits():
    """The limiter costs nothing until it is actually needed."""
    limiter, clock = _paced(5)
    assert [limiter.acquire() for _ in range(5)] == [0.0] * 5
    assert clock.slept == []


def test_the_caller_blocks_once_the_quota_is_spent():
    limiter, clock = _paced(3, window=10)
    for _ in range(3):
        assert limiter.acquire() == 0.0
    assert limiter.acquire() == pytest.approx(10.0)
    assert clock.t == pytest.approx(10.0)


def test_it_waits_exactly_until_the_oldest_start_leaves_the_window():
    """One slot frees at a time, each `window` after the start that holds it."""
    limiter, clock = _paced(2, window=10)
    assert limiter.acquire() == 0.0  # t=0
    clock.advance(4)
    assert limiter.acquire() == 0.0  # t=4
    clock.advance(1)

    # t=5, both slots held. The one taken at t=0 frees at t=10.
    assert limiter.acquire() == pytest.approx(5.0)
    assert clock.t == pytest.approx(10.0)

    # Slots are now held by t=4 and t=10; the next frees at t=14.
    assert limiter.acquire() == pytest.approx(4.0)
    assert clock.t == pytest.approx(14.0)


def test_the_window_slides_rather_than_resetting():
    """
    A fixed window lets a caller spend its whole allowance in the last second
    of one window and the next window's in the first second of the following
    one — twice the limit across that boundary. A sliding window does not.
    """
    limiter, clock = _paced(3, window=60)
    clock.advance(59)
    for _ in range(3):
        assert limiter.acquire() == 0.0  # three starts at t=59

    clock.advance(1)  # t=60 — where a fixed window would reset
    assert limiter.acquire() == pytest.approx(59.0)
    assert clock.t == pytest.approx(119.0)


def test_time_spent_elsewhere_frees_slots():
    """A caller slower than the quota is never made to wait for it."""
    limiter, clock = _paced(2, window=10)
    for _ in range(10):
        assert limiter.acquire() == 0.0
        clock.advance(10)
    assert clock.slept == []


def test_several_slots_can_free_at_once():
    limiter, clock = _paced(3, window=10)
    for _ in range(3):
        _ = limiter.acquire()  # three starts at t=0
    clock.advance(10)  # all three leave the window together
    assert [limiter.acquire() for _ in range(3)] == [0.0] * 3


def test_recorded_starts_do_not_accumulate():
    """Expired starts are dropped, so a long-running caller doesn't leak."""
    limiter, clock = _paced(4, window=10)
    for _ in range(200):
        _ = limiter.acquire()
        clock.advance(3)
    assert len(limiter._starts) <= 4


def test_concurrent_callers_share_one_quota():
    """
    The quota belongs to the service, not to the thread. With 12 requests, a
    limit of 3 and a real window, four batches must run: the last acquisition
    cannot happen before three windows have elapsed.
    """
    window = 0.05
    limiter = RateLimiter(3, window=window)
    done = threading.Barrier(13)

    def worker() -> None:
        _ = limiter.acquire()
        _ = done.wait()

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    _ = done.wait()
    for thread in threads:
        thread.join()

    assert time.monotonic() - started >= 3 * window


# ----------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_is_rejected(limit: int):
    with pytest.raises(ValueError, match="must be positive"):
        _ = RateLimiter(limit)


@pytest.mark.parametrize("window", [0.0, -1.0])
def test_a_non_positive_window_is_rejected(window: float):
    with pytest.raises(ValueError, match="must be positive"):
        _ = RateLimiter(60, window=window)


def test_the_limit_is_exposed():
    assert RateLimiter(42).requests_per_minute == 42


# ----------------------------------------------------------------------------
# Wiring: pacing is dead code unless the request layer and the services
# actually install it.
# ----------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status
        self.reason = f"status {status}"


class _FakeHttp:
    """An `http` object that answers every request with `200`."""

    def __init__(self):
        self.attempts = 0

    def request(
        self, uri: str, method: str, *args: Any, **kwargs: Any
    ) -> tuple[_FakeResponse, bytes]:
        self.attempts += 1
        return _FakeResponse(200), b'{"ok": true}'


def _keep_content(response: object, content: bytes) -> bytes:
    return content


def _make_request(build_request: Any) -> RetryingHttpRequest:
    request = build_request(
        _FakeHttp(),
        _keep_content,
        "https://sheets.googleapis.com/v4/spreadsheets/SHEET",
        method="GET",
    )
    assert isinstance(request, RetryingHttpRequest)
    return request


def test_execute_waits_its_turn():
    """The limiter is only real if `execute()` consults it."""
    limiter, clock = _paced(1, window=10)
    build_request = RetryingHttpRequest.builder(0, limiter)

    _ = _make_request(build_request).execute()
    assert clock.t == 0.0

    _ = _make_request(build_request).execute()
    assert clock.t == pytest.approx(10.0)


def test_execute_is_unpaced_without_a_limiter():
    build_request = RetryingHttpRequest.builder(0)
    request = _make_request(build_request)
    assert request._limiter is None
    assert _make_request(build_request).execute() == b'{"ok": true}'


def test_every_request_of_a_service_shares_one_limiter():
    """Pacing applies across the service, not per call."""
    limiter, _ = _paced(60)
    build_request = RetryingHttpRequest.builder(0, limiter)
    first = _make_request(build_request)
    second = _make_request(build_request)
    assert first._limiter is limiter
    assert second._limiter is limiter


def _stub_discovery(module: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replaces `build` in [module] with a stub, and returns the dict that
    captures the keyword arguments the service passed to it."""
    captured: dict[str, Any] = {}

    def fake_build(*args: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _bare_resource()

    monkeypatch.setattr(f"{module}.build", fake_build)
    return captured


def _bare_resource() -> MagicMock:
    """A resource mock seeded just enough for `DriveService.__init__`, which
    bootstraps its root folder eagerly. Gmail and Sheets ignore it."""
    resource = MagicMock()
    resource.files.return_value.get.return_value.execute.return_value = {
        "id": "USERDRIVE",
        "name": "My Drive",
        "mimeType": "application/vnd.google-apps.folder",
    }
    resource.drives.return_value.list.return_value.execute.return_value = {"drives": []}
    return resource


def _installed_limiter(captured: dict[str, Any]) -> RateLimiter | None:
    """The limiter bound to the requests the captured builder produces."""
    return _make_request(captured["requestBuilder"])._limiter


def test_sheets_is_paced_by_default(monkeypatch: pytest.MonkeyPatch):
    """The Sheets quota is low enough that pacing has to be the default."""
    captured = _stub_discovery("gservices.sheets.sheets_service", monkeypatch)
    _ = SheetsService.build(MagicMock(), MagicMock())
    limiter = _installed_limiter(captured)
    assert limiter is not None
    assert limiter.requests_per_minute == DEFAULT_REQUESTS_PER_MINUTE


def test_sheets_pacing_can_be_raised(monkeypatch: pytest.MonkeyPatch):
    captured = _stub_discovery("gservices.sheets.sheets_service", monkeypatch)
    _ = SheetsService.build(MagicMock(), MagicMock(), requests_per_minute=300)
    limiter = _installed_limiter(captured)
    assert limiter is not None
    assert limiter.requests_per_minute == 300


def test_sheets_pacing_can_be_turned_off(monkeypatch: pytest.MonkeyPatch):
    captured = _stub_discovery("gservices.sheets.sheets_service", monkeypatch)
    _ = SheetsService.build(MagicMock(), MagicMock(), requests_per_minute=None)
    assert _installed_limiter(captured) is None


@pytest.mark.parametrize(
    ("module", "build_service"),
    [
        ("gservices.drive.drive_service", lambda: DriveService.build(MagicMock())),
        ("gservices.gmail.gmail_service", lambda: GmailService.build(MagicMock())),
    ],
)
def test_drive_and_gmail_are_unpaced_by_default(
    module: str, build_service: Any, monkeypatch: pytest.MonkeyPatch
):
    """Their quotas are in the thousands; pacing them would only slow them."""
    captured = _stub_discovery(module, monkeypatch)
    _ = build_service()
    assert _installed_limiter(captured) is None


@pytest.mark.parametrize(
    ("module", "build_service"),
    [
        (
            "gservices.drive.drive_service",
            lambda: DriveService.build(MagicMock(), requests_per_minute=120),
        ),
        (
            "gservices.gmail.gmail_service",
            lambda: GmailService.build(MagicMock(), requests_per_minute=120),
        ),
    ],
)
def test_drive_and_gmail_can_opt_in(
    module: str, build_service: Any, monkeypatch: pytest.MonkeyPatch
):
    captured = _stub_discovery(module, monkeypatch)
    _ = build_service()
    limiter = _installed_limiter(captured)
    assert limiter is not None
    assert limiter.requests_per_minute == 120


def test_the_sheets_rate_reaches_the_service(monkeypatch: pytest.MonkeyPatch):
    """`GoogleServices` owns the knob; the service it builds must honour it."""
    captured: list[int | None] = []

    def fake_build(
        credentials: Any,
        google: Any,
        num_retries: int,
        requests_per_minute: int | None,
    ) -> SheetsService:
        captured.append(requests_per_minute)
        return cast(SheetsService, MagicMock())

    monkeypatch.setattr(SheetsService, "build", fake_build)

    _ = GoogleServices(MagicMock()).Sheets
    _ = GoogleServices(MagicMock(), sheets_requests_per_minute=300).Sheets
    _ = GoogleServices(MagicMock(), sheets_requests_per_minute=None).Sheets
    assert captured == [DEFAULT_REQUESTS_PER_MINUTE, 300, None]


def test_one_service_is_built_once_so_the_quota_is_not_split(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Two `SheetsService` objects would each get their own limiter and together
    spend twice the quota. `GoogleServices` caches, so they can't.
    """
    captured = _stub_discovery("gservices.sheets.sheets_service", monkeypatch)
    google = GoogleServices(MagicMock())
    assert google.Sheets is google.Sheets
    assert _installed_limiter(captured) is not None
