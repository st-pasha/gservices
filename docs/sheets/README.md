# Google Sheets

A typed Python wrapper around the Google Sheets API. Designed for backend
workflows: open a spreadsheet, read and mutate it through familiar Python
objects, flush changes in a batch.

## Quick start

```python
from gservices import GoogleServices

google = GoogleServices(credentials)
spreadsheet = google.Sheets.open("1abc...XYZ")

# Read
print(spreadsheet.title)
for sheet in spreadsheet.sheets:
    print(sheet.title, len(sheet.rows), "x", sheet.column_count)

# Read a cell
cell = spreadsheet.sheet("Summary").cell(0, 0)
print(cell.value, cell.formatted_value)

# Mutate (queued, not sent yet)
cell.value = "done"
cell.format.is_bold = True

# Flush to the server
spreadsheet.save()
```

## Object model

```
GoogleServices.Sheets        →  SheetsService          entry point
    .open(file_id)           →  Spreadsheet            one document
        .sheets[i] / .sheet(name)
                             →  Sheet                  one tab
            .rows[i]         →  Row                    one row
            .columns[i]      →  Column                 one column
            .cell(r, c)      →  Cell                   one cell
                .format      →  CellFormat             cell's formatting
                    .border_top, ...
                             →  BorderFormat           one edge
            .metadata        →  SheetDeveloperMetadata anchored key/value
        .metadata            →  SpreadsheetDeveloperMetadata
```

The boundary at `Spreadsheet` matches the API's batch boundary — every mutation
inside a `Spreadsheet` queues a request, and `Spreadsheet.save()` sends them all
in a single `batchUpdate`.

## Read paths

Three different "values" live on each cell. They're not interchangeable:

| Property | Type | Meaning |
|---|---|---|
| `cell.user_entered_value` | `str / float / bool / Formula / ErrorValue / None` | What the user typed (or wrote via the API). For a formula cell, the `Formula("=A1+1")` itself. |
| `cell.effective_value` | same | The computed result. For a formula, the evaluated number/string. |
| `cell.formatted_value` | `str` | The rendered text the user sees in the UI (e.g. `"$1,234.56"`, `"42%"`). |

`cell.value` is an alias for `effective_value`. See [cells.md](cells.md) for
detail.

## Batching model

Mutations don't go to the server immediately:

```python
cell.value = "x"      # queues an updateCells request
cell.format.is_bold = True  # queues another (or merges with the first)
sheet.add_row()       # queues an insertDimension request
spreadsheet.save()    # one batchUpdate sends them all
```

This is faster (one round-trip) and respects API rate limits, but means
**reading a cell right after writing reflects only the local cache**, not the
server. The local cache is kept in sync where possible (e.g. `cell.value =
"hi"` updates `cell.effective_value` immediately), but for some mutations — most
notably formulas — the post-write `effective_value` is `None` until the
spreadsheet is reloaded.

See [spreadsheet.md](spreadsheet.md) for `save()` semantics.

## Documentation map

- [**spreadsheet.md**](spreadsheet.md) — `SheetsService.open`, the `Spreadsheet`
  container, add/delete/move sheets, the batching model
- [**sheets.md**](sheets.md) — `Sheet` properties, rows and columns, merges,
  freezing, gridlines
- [**cells.md**](cells.md) — `Cell` reads and writes, formulas, hyperlinks,
  error values
- [**formatting.md**](formatting.md) — `CellFormat`, `BorderFormat`, number
  formats, colors, fonts
- [**metadata.md**](metadata.md) — `DeveloperMetadata` at every level
  (spreadsheet, sheet, row, column)
- [**concurrency.md**](concurrency.md) — `save(check_version=True)`,
  `exclusive_edit()`, what they catch and what they don't
- [**snapshot.md**](snapshot.md) — `Spreadsheet.snapshot()` / `save_snapshot()`,
  the git-diff-friendly format

## What this library doesn't do

- **No bulk-write API.** Each `cell.value = ...` is one `updateCells` request.
  Sequential identical-format writes to adjacent cells merge automatically (see
  `merge_requests` in `utils.py`), but writing 1000 distinct cells is still
  1000 requests. If you need bulk writes, consider batching with
  `values.batchUpdate` directly via `SheetsService.resource`.
- **No charts, pivot tables, data validation, conditional formatting, filter
  views, banded ranges, slicers.** The data model wraps cells, formats, rows,
  columns, merges, borders, and metadata. The other features pass through the
  API but aren't exposed in the wrapper.
- **No transactional updates.** Sheets has no compare-and-swap. The library
  offers two opt-in safety nets — version checking and protected-range locking
  — see [concurrency.md](concurrency.md).
- **No time travel.** Drive revisions exist but Sheets-native API doesn't
  support reading at a specific revision; only XLSX exports are revision-aware.
