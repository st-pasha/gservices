# Retries

Google's APIs fail transiently. A backend hiccups and returns
`503 The service is currently unavailable`; a burst of calls trips a per-minute
quota and comes back `429`; a socket dies mid-response. None of these mean the
request was wrong — repeating it a moment later usually just works.

Every service built by `gservices` retries those failures on its own. A caller
does nothing to opt in, and a long-running job (walking a Drive folder,
snapshotting a hundred spreadsheets) no longer dies on the one call that
happened to land during a blip.

## What gets retried

The underlying `googleapiclient` decides, and it retries:

- any **5xx** response — `500`, `502`, `503`, `504`;
- **`429 Too Many Requests`** — the rate-limit response;
- **`403`** responses whose reason is a rate limit (`rateLimitExceeded`,
  `userRateLimitExceeded`), told apart from ordinary permission denials by the
  reason in the response body;
- transport-level failures: SSL errors, socket timeouts, connection
  reset / refused / aborted, DNS resolution failures.

Everything else is raised on the first attempt, because retrying it cannot
help: a `404` for a file that does not exist, a `403` for a file you may not
read, a malformed request. A transient failure that outlives the retry budget
is raised too — as the `HttpError` of the *last* attempt.

## How long it waits

Before retry `k` the client sleeps a random interval in `[0, 2**k)` seconds:
up to 2s before the first retry, 4s before the second, and so on. Randomising
the wait keeps a fleet of clients from re-converging on the server in lockstep.

With the default of 5 retries a doomed call takes at most ~62s (~31s on
average) before it gives up — long enough to ride out a typical backend blip,
short enough that an interactive script does not look hung.

## Configuring it

The count is a constructor argument, and it flows to every service the object
builds:

```python
from gservices import GoogleServices

google = GoogleServices(credentials)                  # 5 retries, the default
patient = GoogleServices(credentials, num_retries=10) # for a long batch job
strict = GoogleServices(credentials, num_retries=0)   # fail on first error
```

`GoogleServices.connect()`, `.from_file()`, `.from_service_account_file()` and
`.from_service_account_info()` all take the same `num_retries` argument, as do
`DriveService.build()`, `GmailService.build()` and `SheetsService.build()` if
you construct a single service directly.

`num_retries=0` restores the stock `googleapiclient` behaviour — one attempt,
no waiting. Reach for it when a human is watching and would rather see the
error now, or when you are driving retries yourself at a higher level.

## Watching it happen

Retries are silent by default. `googleapiclient` logs one warning per retry, so
turning that logger up shows them:

```python
import logging

logging.getLogger("googleapiclient.http").setLevel(logging.WARNING)
# Sleeping 1.61 seconds before retry 1 of 5 for request: GET https://...
```

## Caveats

- **Idempotency.** A request that reached the server and failed on the way back
  is sent again. Reads and the API's idempotent writes are safe; if you route a
  non-idempotent operation through the `.resource` escape hatch, consider
  whether a duplicate would hurt, and drop to `num_retries=0` if so.
- **Batch requests** (`BatchHttpRequest`, constructed directly rather than
  through the service) do not pick this up — they take their own
  `num_retries` argument.
- **Quotas are not capacity.** Retrying a `429` rides out a burst; it will not
  get you through a quota that is simply too small. Slowing the caller down is
  a separate mechanism — see [rate-limiting.md](rate-limiting.md), which paces
  Sheets requests by default for exactly this reason.

## Implementation

`RetryingHttpRequest` (in `gservices/retrying_http_request.py`) subclasses
`googleapiclient.http.HttpRequest` and binds the retry count at construction,
so `execute()` applies it without every call site having to pass it. Each
service installs it by passing `RetryingHttpRequest.builder(n)` as the
`requestBuilder` to `googleapiclient.discovery.build()`, which means every
request the service issues — including requests made through the `.resource`
escape hatch — retries.
