# `Spreadsheet`

The `Spreadsheet` object is one Google Sheets document, identified by its
Drive file ID. All mutations queue against it; `save()` flushes them.

## Opening

```python
from gservices import GoogleServices

google = GoogleServices(credentials)

# Lightweight: fetches sheet names and properties only.
ss = google.Sheets.open("1abc...XYZ")

# Heavyweight: also loads grid data (cell values, formats) for every sheet.
ss = google.Sheets.open("1abc...XYZ", load=True)

# With concurrent-edit detection enabled — see concurrency.md.
ss = google.Sheets.open("1abc...XYZ", track_version=True)

# Reading only the part of each sheet that holds data — see below.
ss = google.Sheets.open("1abc...XYZ", extent="data")
```

`load=True` is one large fetch that includes every cell, format, note, and
hyperlink. For a spreadsheet with many big sheets this can take seconds and
return megabytes of JSON. Prefer `load=False` and let individual `Sheet`
objects load their grid data on demand (the first cell access triggers a
single-sheet fetch).

If you know you'll touch many sheets, `load=True` is one round-trip vs. one
per sheet — usually faster overall, despite the bigger payload.

## How much of a sheet to read: `extent`

A sheet's *grid* is every cell it has — 1000 × 26 for a blank one, and far
more for a sheet someone has widened. Its *data extent* is the rectangle that
actually holds values. In real documents the second is a tiny fraction of the
first, and the difference is expensive, because an empty cell is not free:
it comes back carrying the format it inherits, which is a few kilobytes of
nested objects once parsed.

```python
ss = google.Sheets.open(file_id, extent="data")   # the populated rectangle
ss = google.Sheets.open(file_id)                  # the declared grid (default)
```

Measured on a 29-tab delivery log whose tabs are 1000 rows deep and up to 118
columns wide:

| | cells fetched | peak memory | wall clock | snapshot |
|---|---|---|---|---|
| `extent="grid"` | 1,044,206 | 7,376 MB | 37.4 s | 0.66 MB |
| `extent="data"` | 12,996 | 427 MB | 6.5 s | 0.60 MB |

The snapshots are the same document. What the smaller one leaves out is the
formatting of cells that hold nothing — 161 format groups out of 1,948, each
one a run of empty cells styled like their neighbours. Values, formulas,
notes, hyperlinks, merges, row and column properties, and developer metadata
are identical, and so is the format of every cell that holds a value.

So: prefer `extent="data"` unless the formatting of empty cells is part of
what you are reading — a colour-coded empty column, a ruled-off block below a
table. Row heights, hidden rows and developer metadata are **not** bounded
away; those are read for the whole sheet either way, because a row id pinned
to an empty row is a normal thing to keep there and re-writing it on every
load would be worse than the fetch it saves.

The mode costs three requests where the default makes one — the values (which
is also how the API is asked where the data ends), the bounded grid, and the
dimension properties — all of them small. They are paid per *load*, not per
sheet, so load the sheets you need together (`snapshot()` does, and so does
`open(load=True)`) rather than letting them trickle in one at a time. It is set when the spreadsheet is
opened and fixed thereafter, so every grid this object loads is bounded the
same way, `sheet.load()` and `snapshot()` included.

## Properties

```python
ss.id              # str  — same as the Drive file ID
ss.url             # str  — the docs.google.com URL
ss.title           # str  — get and set
ss.locale          # str  — e.g. "en_US"; get and set
ss.time_zone       # str  — IANA tz name; get and set
ss.theme           # gs.SpreadsheetTheme — fonts and theme colors
ss.default_cell_format  # CellFormat — the spreadsheet-level default
ss.file            # SpreadsheetFile — Drive-side wrapper (for sharing, etc.)
ss.sheets          # Sequence[Sheet] — all tabs, in index order
ss.visible_sheets  # list[Sheet] — excludes hidden tabs
ss.metadata        # SpreadsheetDeveloperMetadata — see metadata.md
```

Setters on `title`, `locale`, `time_zone` queue `updateSpreadsheetProperties`
requests.

## Looking up a sheet

```python
# By index — always works, never raises (list-style access).
first = ss.sheets[0]

# By name — returns None if not found.
summary = ss.sheet("Summary")
if summary is None:
    raise ValueError("No Summary tab")
```

There's no dict-style `ss["Summary"]` access — use `ss.sheet("Summary")` and
handle the `None` case explicitly.

## Adding, deleting, moving sheets

```python
# Bare add — appends at the end.
new = ss.add_sheet("Q4")

# With grid sizing and visual properties.
new = ss.add_sheet(
    "Q4",
    row_count=500,
    column_count=10,
    tab_color="#ff8800",
    hide_gridlines=True,
)

# Delete by name or by Sheet object.
ss.delete_sheet("Old")
ss.delete_sheet(ss.sheets[2])

# Move — reorder sheets within the spreadsheet.
ss.move_sheet("Summary", after="Q3")
ss.move_sheet(summary_sheet, before=0)  # to index 0
```

`add_sheet` queues an `addSheet` request and locally invents a `sheetId` as
`max(existing) + 1`. If the server reassigns the ID (which it occasionally
does), a response callback patches the local `Sheet.id` after `save()`.
Between `add_sheet()` and `save()`, the local ID is used for any queued
operations targeting the new sheet — those operations succeed if the server
honors the suggested ID, and fail if it doesn't.

`delete_sheet` accepts either a name or a `Sheet` object. Missing name →
`KeyError`.

## `save()` and the batching model

```python
ss.save()                    # flush all queued changes
ss.save(check_version=True)  # flush, but bail if file changed externally
```

Internally:
1. Queued requests are sliced into batches of 500 (`Spreadsheet.BATCH_SIZE`).
2. Each batch is sent as one `spreadsheets.batchUpdate` call.
3. Response callbacks fire (e.g. `add_sheet`'s ID reconciliation).
4. Queue is cleared.

If `save()` raises an exception mid-flight, partially-completed batches stay
applied on the server, and unsent batches remain in the local queue. The
typical recovery is `ss.save()` again — the unsent batches go through.

**Request merging.** When you queue an update that's adjacent to the previous
queued update on the same cell/range, `_add_request` tries to merge them via
`utils.merge_requests`. So:

```python
sheet.cell(0, 0).value = "x"   # queued
sheet.cell(0, 0).note = "n"    # merged into the first updateCells request
```

becomes one request, not two. The merge is conservative — only sequential
identical-target writes merge.

**What `save()` returns.** Nothing. To verify success, watch for exceptions
(the underlying `googleapiclient` raises on HTTP errors) and inspect
spreadsheet state after.

## `reload()`

```python
ss.reload()                     # refresh cell data for all loaded sheets
ss.reload(include_computed=True)  # also fetch computed (formula) values
```

`reload()` re-fetches cell data, in one batched API call, for every sheet
that already had data loaded. Sheets that were never loaded are not
eager-fetched (`reload()` respects the lazy-load opt-in from `open()`).

Use it when you suspect local state is stale relative to the server. The
canonical case is inside an `exclusive_edit()` block — see
[concurrency.md](concurrency.md).

**Important: existing `Cell`, `Row`, and `Column` references are stale
after `reload()`.** Their backing `_data` points at the discarded
`_cell_data`. Re-fetch via `sheet.cell(...)`, `sheet.rows[...]`,
`sheet.columns[...]`:

```python
old_cell = ss.sheets[0].cell(0, 0)
ss.reload()
# DON'T: old_cell.value = "x"      ← writes through a stale reference
# DO:    ss.sheets[0].cell(0, 0).value = "x"
```

**What `reload()` doesn't refresh**: spreadsheet properties (title, locale,
theme), sheet developer metadata, merged ranges, protected ranges, charts,
or other sub-objects this wrapper doesn't model. For a fully fresh view,
re-open via `Sheets.open(id)`.

## The Drive side

`ss.file` gives you the corresponding `SpreadsheetFile` from the Drive
service, useful for:
- Sharing changes (`ss.file.share(...)`)
- Trashing (`ss.file.trash()`)
- Reading `created_time`, `modified_time`, `version`
- Listing parent folders

The `Spreadsheet` and `SpreadsheetFile` views of the same document share state
where they overlap (file ID) but maintain separate data caches.

## Lifecycle

A `Spreadsheet` object lives until garbage-collected. There's no explicit
close — TCP connections are managed by `googleapiclient`. Multiple
`Spreadsheet` instances for the same file ID don't share state; mutations on
one don't appear in the other until you `save()` on the first and re-open the
second. Sharing a single instance across a workflow is the intended pattern.

Don't share a `Spreadsheet` across threads — the request queue isn't
synchronized. If you need parallel work, open separate `Spreadsheet`
instances per thread.

## See also

- [sheets.md](sheets.md) — the `Sheet` API
- [concurrency.md](concurrency.md) — `save(check_version=True)` and
  `exclusive_edit()` for multi-writer safety
- [snapshot.md](snapshot.md) — `ss.snapshot()` / `ss.save_snapshot(path)`
