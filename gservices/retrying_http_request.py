from collections.abc import Callable
from typing import Any

from googleapiclient.http import HttpRequest

from gservices.rate_limiter import RateLimiter

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

    Optionally the same seam also *paces* requests. Retrying handles a burst;
    it cannot get a caller through a quota that is simply too small, and a job
    that steadily overspends its allowance dies once some call exhausts its
    retry budget. Passing a `RateLimiter` makes every request wait its turn
    instead, so the service runs at the quota rather than into it.
    """

    @staticmethod
    def builder(
        num_retries: int = DEFAULT_NUM_RETRIES,
        limiter: RateLimiter | None = None,
    ) -> Callable[..., HttpRequest]:
        """
        Returns a `requestBuilder` for `googleapiclient.discovery.build()`
        whose requests retry up to [num_retries] times.

        Pass `num_retries=0` to get the stock, no-retry behaviour back.

        Every request built by the returned callable shares [limiter], so the
        pacing applies across the service rather than per call. `None` — the
        default — means no pacing.
        """
        if num_retries < 0:
            raise ValueError(f"`num_retries` must be non-negative, got {num_retries}")

        def build_request(*args: Any, **kwargs: Any) -> HttpRequest:
            return RetryingHttpRequest(
                *args, num_retries=num_retries, limiter=limiter, **kwargs
            )

        return build_request

    def execute(self, http: Any = None, num_retries: int | None = None) -> Any:
        """
        Executes the request, waiting its turn and retrying transient failures.

        [num_retries] overrides the count bound at construction for this one
        call; when it is None (the default) the bound count applies.

        Pacing covers the first attempt only. Retries are driven by the client
        underneath and back off on their own — and if the limiter is doing its
        job there are no rate-limit retries left to pace.
        """
        if num_retries is None:
            num_retries = self._num_retries
        if self._limiter is not None:
            _ = self._limiter.acquire()
        return super().execute(http=http, num_retries=num_retries)

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def __init__(
        self,
        *args: Any,
        num_retries: int = DEFAULT_NUM_RETRIES,
        limiter: RateLimiter | None = None,
        **kwargs: Any,
    ):
        """
        Wraps `HttpRequest`, binding [num_retries] and [limiter] to every
        `execute()` call.

        The remaining arguments are passed through untouched. Typically you
        don't call this directly — `googleapiclient` does, via the callable
        returned by `builder()`.
        """
        super().__init__(*args, **kwargs)
        self._num_retries = num_retries
        self._limiter = limiter

        # `HttpRequest.__init__` assigns these two — `time.sleep` and
        # `random.random`, which the retry loop uses to space out attempts —
        # and documents them as seams for testing. The type stubs omit them, so
        # re-declare them here to keep substituting them type-safe.
        self._sleep: Callable[[float], None]
        self._rand: Callable[[], float]
