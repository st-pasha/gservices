from collections.abc import Callable
from typing import Any

from googleapiclient.http import HttpRequest

# How many times a request is retried when no count is given explicitly.
# Backoff before attempt `k + 1` is a random wait in `[0, 2**k)` seconds, so
# five retries spend at most ~62s (~31s on average) waiting before giving up —
# long enough to ride out a typical backend blip, short enough that an
# interactive script does not appear hung.
DEFAULT_NUM_RETRIES = 5


class RetryingHttpRequest(HttpRequest):
    """
    A `googleapiclient` request that retries transient failures on its own.

    The Google API client already knows how to retry: `HttpRequest.execute()`
    takes a `num_retries` argument and backs off exponentially between
    attempts. But it defaults to `0`, so every call site has to opt in
    individually — and one that forgets turns a momentary server hiccup into a
    failed operation. This subclass binds the retry count at *construction*
    time instead: a service built with `RetryingHttpRequest.builder(n)` as its
    `requestBuilder` retries every request it issues, including requests made
    through the `.resource` escape hatch.

    What counts as transient is the API client's own judgement
    (`googleapiclient.http._should_retry_response`): any 5xx response — notably
    the `503 The service is currently unavailable` that Google returns when a
    backend hiccups — plus `429 Too Many Requests`, `403`s whose reason is a
    rate limit, and socket / SSL-level transport errors. Everything else (a
    missing file, a permission denial, a malformed request) is raised on the
    first attempt, as is a transient failure that outlives the retry budget.

    Retries are not free of side effects: a request that reached the server and
    failed on the way back is sent again. Every call the wrapper itself makes is
    a read or an idempotent write, so a duplicate is harmless — but keep it in
    mind before routing a non-idempotent operation through a retrying service.
    """

    @staticmethod
    def builder(
        num_retries: int = DEFAULT_NUM_RETRIES,
    ) -> Callable[..., HttpRequest]:
        """
        Returns a `requestBuilder` for `googleapiclient.discovery.build()`
        whose requests retry up to [num_retries] times.

        Pass `num_retries=0` to get the stock, no-retry behaviour back.
        """
        if num_retries < 0:
            raise ValueError(f"`num_retries` must be non-negative, got {num_retries}")

        def build_request(*args: Any, **kwargs: Any) -> HttpRequest:
            return RetryingHttpRequest(*args, num_retries=num_retries, **kwargs)

        return build_request

    def execute(self, http: Any = None, num_retries: int | None = None) -> Any:
        """
        Executes the request, retrying transient failures.

        [num_retries] overrides the count bound at construction for this one
        call; when it is None (the default) the bound count applies.
        """
        if num_retries is None:
            num_retries = self._num_retries
        return super().execute(http=http, num_retries=num_retries)

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def __init__(
        self, *args: Any, num_retries: int = DEFAULT_NUM_RETRIES, **kwargs: Any
    ):
        """
        Wraps `HttpRequest`, binding [num_retries] to every `execute()` call.

        The remaining arguments are passed through untouched. Typically you
        don't call this directly — `googleapiclient` does, via the callable
        returned by `builder()`.
        """
        super().__init__(*args, **kwargs)
        self._num_retries = num_retries

        # `HttpRequest.__init__` assigns these two — `time.sleep` and
        # `random.random`, which the retry loop uses to space out attempts —
        # and documents them as seams for testing. The type stubs omit them, so
        # re-declare them here to keep substituting them type-safe.
        self._sleep: Callable[[float], None]
        self._rand: Callable[[], float]
