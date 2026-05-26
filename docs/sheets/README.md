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

## Feature coverage

### Supported

- **Cell content** — strings, numbers, booleans, formulas, error values,
  notes, hyperlinks, dates / times / datetimes (with proper number formats)
- **Cell formatting** — number formats (TEXT / NUMBER / PERCENT / CURRENCY
  / DATE / TIME / DATE_TIME / SCIENTIFIC with patterns), background and
  foreground colors (hex or theme), padding, horizontal and vertical
  alignment, wrap strategy, font family, font size, bold, italic,
  underline, strikethrough
- **Borders** — all four edges (top / right / bottom / left), every style
  (DOTTED / DASHED / SOLID / SOLID_MEDIUM / SOLID_THICK / DOUBLE), width
  and color
- **Merged cells** — read and create
- **Row and column structure** — heights and widths, hidden state,
  insert / move / remove / sort
- **Sheet structure** — add / delete / move / rename, hidden state, tab
  color, hide gridlines, frozen rows / columns, max grid size
- **Document properties** — title, locale, time zone, theme, default cell
  format
- **Developer metadata** — at spreadsheet, sheet, row, and column scope;
  persists across structural changes (row inserts, sorts, moves)
- **Concurrency safety nets** — version-based change detection
  (`save(check_version=True)`) and protected-range locking
  (`exclusive_edit()`)
- **Snapshots** — git-diff-friendly JSON export of the entire spreadsheet
  state

### Not supported

- **Charts** — column / line / pie / scatter, etc. Pass through the API
  but no wrapper.
- **Pivot tables**
- **Slicers**
- **Banded ranges** — alternating row colors
- **Conditional formatting** — color scales, data bars, rule-based cell
  highlighting
- **Data validation** — dropdowns, checkboxes, custom validation rules
- **Filter views** and **basic filter**
- **Row / column groups** — collapsible outlines
- **Named ranges**
- **Smart chips** — people chips, file chips, calendar chips, finance
  chips, place chips
- **Drop-down chips** — the new in-cell chip-style dropdowns
- **Embedded images** — both in-cell (`IMAGE()` and pasted) and
  over-the-grid images
- **Rich text within a cell** — multiple fonts, colors, or links within
  the same cell (`textFormatRuns` is read but only the cell-level format
  is preserved)
- **Bulk range I/O** — `values.batchGet`, `values.batchUpdate`, and
  `values.append` aren't wrapped; each `cell.value = ...` queues its own
  `updateCells` request, so writing 1000 distinct cells is 1000 queued
  requests (adjacent identical-shape writes do auto-merge — see
  `utils.merge_requests`). `sheet.rows.insert()` appends a single empty
  row at the bottom of the data region, but appending many rows of data
  in one HTTP call needs `values.append`.
- **Find and replace**, **autoresize columns**, **copy/duplicate sheet** —
  common ops without wrappers (use raw API via `SheetsService.resource`)

### Architectural limits

- **No transactional updates.** Sheets has no compare-and-swap primitive.
  Both opt-in safety nets (`check_version`, `exclusive_edit`) are
  best-effort, not transactional. See [concurrency.md](concurrency.md).
- **No time travel.** Drive revisions exist, but the Sheets-native API
  doesn't support reading at a specific revision — only XLSX / PDF exports
  are revision-aware.
