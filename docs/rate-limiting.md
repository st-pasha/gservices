# Rate limiting

Google's APIs meter you as well as fail on you. Beyond a certain number of
requests per minute they stop answering and return `429 Too Many Requests` —
not because the request was wrong, but because you asked too often.

[Retrying](retries.md) answers a *burst*: something momentarily went wrong, so
wait and ask again. It cannot answer a *quota*. A caller that steadily asks for
more than it is allowed gets `429`s no matter how patiently it retries — the
backoff just spreads the same excess over a longer wall clock, and eventually
one call runs out of retry budget and takes the whole job with it.

The only fix is to ask more slowly. Services built by `gservices` can do that
for you: every request waits its turn, so the service runs *at* the quota
instead of crashing into it.

## Why Sheets is paced and the others aren't

Quotas differ by two orders of magnitude:

| API | Per-user quota | Paced by default |
|---|---|---|
| Sheets | 60 reads / minute, 60 writes / minute | **yes**, at 60/min |
| Drive | ~12 000 / minute | no |
| Gmail | ~15 000 quota units / minute | no |

A single loop over the tabs of one workbook reaches the Sheets limit. Nothing
this wrapper does on its own comes close to Drive's or Gmail's, so pacing those
would only add latency for no benefit — the knob is there if you want it, but
it is off.

## Configuring it

```python
from gservices import GoogleServices

google = GoogleServices(credentials)                              # 60/min, the default
faster = GoogleServices(credentials, sheets_requests_per_minute=300)  # a raised quota
unpaced = GoogleServices(credentials, sheets_requests_per_minute=None) # no pacing
```

`GoogleServices.connect()`, `.from_file()`, `.from_service_account_file()` and
`.from_service_account_info()` all take the same `sheets_requests_per_minute`
argument.

Building a single service directly gives you the same knob, and lets you pace
Drive or Gmail too:

```python
from gservices.drive.drive_service import DriveService
from gservices.sheets.sheets_service import SheetsService

sheets = SheetsService.build(credentials, google, requests_per_minute=120)
drive = DriveService.build(credentials, requests_per_minute=600)
```

Raise the number if your quota is raised (Google grants per-project increases
on request). Lower it if you are sharing the quota with something else — a
second process, a cron job, a colleague's script — since the limiter only knows
about the requests that go through it.

`None` sends requests as fast as they are made, which is what you want when a
higher layer is already pacing, or when a human is watching a handful of calls
and would rather not wait.

## How it interacts with retries

The two are complementary, and both are on by default for Sheets:

- **pacing** keeps the service inside the quota, so `429`s don't happen;
- **retrying** covers the failures that happen anyway — backend blips, dropped
  sockets, and the `429`s from whatever else is spending the same quota.

Pacing applies to the first attempt of each request. Retries are driven by
`googleapiclient` underneath and back off on their own; if the limiter is doing
its job there are no rate-limit retries left to pace.

## Spending fewer requests

Pacing makes a job that overspends its quota *slow* rather than *broken*. Not
issuing the request in the first place is better still.

`Spreadsheet.save()` already batches every queued mutation into one
`batchUpdate` — see [sheets/spreadsheet.md](sheets/spreadsheet.md). On the read
side the trap is subtler, because the order in which you happen to touch a
sheet decides how many requests it costs:

```python
for sheet in spreadsheet.sheets:
    print(len(sheet.rows))       # fetches values  — one request
    print(sheet.cell(0, 0).value)  # fetches the grid — a second request
```

`sheet.values` (and everything reading the data extent — `rows`, `columns`,
`column_count`) is served by a cheap `values.get`; cells, formats and row
properties need the full grid. Reaching for the cheap one first and the rich
one afterwards costs two round trips per sheet. `Sheet.load()` collapses that
to one by fetching the grid up front:

```python
for sheet in spreadsheet.sheets:
    sheet.load()                   # one request; values come from the grid
    print(len(sheet.rows), sheet.cell(0, 0).value)
```

Everything loads on demand, so `load()` is never *required* — it is a way to
choose which fetch happens, not whether one does.

## Caveats

- **The limiter only knows its own service.** Two `GoogleServices` objects, two
  processes, or a colleague running the same script against the same account
  each pace independently and together overspend the quota. Divide the number
  among them.
- **Reads and writes are metered separately.** Google counts 60 reads *and* 60
  writes per minute; the limiter counts requests, without telling them apart.
  At the default that is safe — 60 of anything is inside both — but a raised
  limit is spent against whichever bucket a request happens to land in.
- **Quotas are per project *and* per user.** The per-project limit is shared by
  everyone using your credentials. Pacing one user's requests does not protect
  a project-wide limit that many users are spending at once.
- **Waiting is not fair.** Under contention, which thread gets the next slot is
  a scheduling accident. Every waiter is sleeping for the same moment and wakes
  at the same time, so this is jitter, not starvation.
- **Batch requests** (`BatchHttpRequest`, constructed directly through the
  `.resource` escape hatch) do not go through the request builder, so they are
  neither paced nor retried.

## Implementation

`RateLimiter` (in `gservices/rate_limiter.py`) keeps the timestamps of the last
`requests_per_minute` starts. `acquire()` blocks until starting a request would
keep them all inside a rolling window, then records the start and returns.

The window *slides* rather than resetting on the minute. A fixed window lets a
caller spend its whole allowance in the last second of one window and the next
window's in the first second of the following one — twice the limit across that
boundary, which is exactly the burst the quota exists to prevent.

One limiter is shared by every request of a service: `SheetsService.build()`
constructs it and hands it to `RetryingHttpRequest.builder()`, which binds it
into every request `googleapiclient.discovery.build()` creates. So the pacing
covers the whole service — including requests made through the `.resource`
escape hatch — rather than each call site separately. It is thread-safe, and
costs nothing while the caller is under quota.
