"""
Unit tests for `gservices.retrying_http_request`. Each test drives a
`RetryingHttpRequest` against a fake `http` object that hands back a scripted
sequence of HTTP statuses, so the retry loop runs for real without touching the
network — or sleeping.
"""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from gservices.drive.drive_service import DriveService
from gservices.gmail.gmail_service import GmailService
from gservices.google_services import GoogleServices
from gservices.retrying_http_request import DEFAULT_NUM_RETRIES, RetryingHttpRequest
from gservices.sheets.sheets_service import SheetsService


class _FakeResponse:
    """Stands in for an `httplib2.Response`: the retry loop and `HttpError`
    between them only read `status` and `reason`."""

    def __init__(self, status: int):
        self.status = status
        self.reason = f"status {status}"


class _FakeHttp:
    """An `http` object that replies with [statuses] in order, one per request."""

    def __init__(self, *statuses: int):
        self._statuses = list(statuses)
        self.attempts = 0

    def request(
        self, uri: str, method: str, *args: Any, **kwargs: Any
    ) -> tuple[_FakeResponse, bytes]:
        self.attempts += 1
        return _FakeResponse(self._statuses.pop(0)), b'{"ok": true}'


def _keep_content(response: object, content: bytes) -> bytes:
    """A `postproc` that hands the raw body back, so a test can assert on it."""
    return content


def _make_request(http: _FakeHttp, num_retries: int) -> RetryingHttpRequest:
    """Builds a request through the public `builder()` seam — the same path
    `googleapiclient.discovery.build()` takes."""
    build_request = RetryingHttpRequest.builder(num_retries)
    request = build_request(
        http,
        _keep_content,
        "https://sheets.googleapis.com/v4/spreadsheets/SHEET",
        method="GET",
    )
    assert isinstance(request, RetryingHttpRequest)
    return _without_backoff(request)


def _no_sleep(seconds: float) -> None:
    pass


def _no_jitter() -> float:
    return 0.0


def _without_backoff(request: RetryingHttpRequest) -> RetryingHttpRequest:
    """Silences the wait between attempts, so the tests run instantly.

    `_sleep` / `_rand` are `HttpRequest`'s own seams for testing — it assigns
    them in its constructor for exactly this purpose."""
    request._sleep = _no_sleep
    request._rand = _no_jitter
    return request


def test_retries_until_the_call_succeeds():
    # The 503 that prompted all this: two blips, then the real answer.
    http = _FakeHttp(503, 503, 200)
    result = _make_request(http, num_retries=5).execute()
    assert result == b'{"ok": true}'
    assert http.attempts == 3


def test_gives_up_once_the_budget_is_spent():
    http = _FakeHttp(503, 503, 503, 503)
    with pytest.raises(HttpError) as excinfo:
        _make_request(http, num_retries=3).execute()
    assert excinfo.value.resp.status == 503
    # The original attempt plus three retries — and no fifth call.
    assert http.attempts == 4


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status: int):
    http = _FakeHttp(status, 200)
    _ = _make_request(http, num_retries=2).execute()
    assert http.attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_raised_immediately(status: int):
    # A missing file or a denied permission will not fix itself; retrying it
    # only delays the error. (Rate-limit 403s are the exception, but they are
    # told apart by their response body, which this fake does not carry.)
    http = _FakeHttp(status)
    with pytest.raises(HttpError):
        _ = _make_request(http, num_retries=5).execute()
    assert http.attempts == 1


def test_zero_retries_restores_stock_behaviour():
    http = _FakeHttp(503)
    with pytest.raises(HttpError):
        _ = _make_request(http, num_retries=0).execute()
    assert http.attempts == 1


def test_per_call_count_overrides_the_bound_one():
    http = _FakeHttp(503, 503, 200)
    request = _make_request(http, num_retries=0)
    assert request.execute(num_retries=2) == b'{"ok": true}'
    assert http.attempts == 3


def test_negative_retry_count_is_rejected():
    with pytest.raises(ValueError, match="must be non-negative"):
        _ = RetryingHttpRequest.builder(-1)


# ----------------------------------------------------------------------------
# Wiring: the retry logic is dead code unless the services actually install it.
# ----------------------------------------------------------------------------


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


def _assert_requests_retry(captured: dict[str, Any]) -> None:
    """Asserts that the captured `requestBuilder` produces retrying requests."""
    http = _FakeHttp(503, 200)
    request = captured["requestBuilder"](http, _keep_content, "https://example/x")
    assert isinstance(request, RetryingHttpRequest)
    _ = _without_backoff(request).execute()
    assert http.attempts == 2


def test_drive_service_installs_the_retrying_builder(monkeypatch: pytest.MonkeyPatch):
    captured = _stub_discovery("gservices.drive.drive_service", monkeypatch)
    _ = DriveService.build(MagicMock())
    _assert_requests_retry(captured)


def test_gmail_service_installs_the_retrying_builder(monkeypatch: pytest.MonkeyPatch):
    captured = _stub_discovery("gservices.gmail.gmail_service", monkeypatch)
    _ = GmailService.build(MagicMock())
    _assert_requests_retry(captured)


def test_sheets_service_installs_the_retrying_builder(monkeypatch: pytest.MonkeyPatch):
    captured = _stub_discovery("gservices.sheets.sheets_service", monkeypatch)
    _ = SheetsService.build(MagicMock(), MagicMock())
    _assert_requests_retry(captured)


def test_num_retries_reaches_the_services(monkeypatch: pytest.MonkeyPatch):
    """`GoogleServices` owns the knob; the services it builds must honour it."""
    captured: list[int] = []

    def fake_build(credentials: Any, num_retries: int) -> DriveService:
        captured.append(num_retries)
        return cast(DriveService, MagicMock())

    monkeypatch.setattr(DriveService, "build", fake_build)

    _ = GoogleServices(MagicMock()).Drive
    _ = GoogleServices(MagicMock(), num_retries=11).Drive
    assert captured == [DEFAULT_NUM_RETRIES, 11]
