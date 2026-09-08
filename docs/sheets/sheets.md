# `Sheet`, `Row`, `Column`

A `Sheet` is one tab inside a `Spreadsheet`. It owns its grid (`rows`,
`columns`, individual cells), its display properties (title, color,
gridlines), and its developer metadata.

## Sheet properties

```python
sheet.title              # str — get and set
sheet.id                 # int — server-assigned sheet ID; stable across moves
sheet.url                # str — deep-link URL (#gid=N)
sheet.index              # int — 0-based position among sibling sheets
sheet.type               # Literal["GRID", "OBJECT", "DATA_SOURCE"]
sheet.hidden             # bool — get and set
sheet.tab_color          # str | None — hex or theme name; get and set
sheet.max_row_count      # int — total grid rows (incl. empty); get and set
sheet.max_column_count   # int — total grid columns; get and set
sheet.frozen_row_count   # int — get and set
sheet.frozen_column_count # int — get and set
sheet.hide_gridlines     # bool — get and set
sheet.spreadsheet        # Spreadsheet — parent
```

`max_row_count` and `max_column_count` are the *grid capacity*, not the
data extent. A blank spreadsheet defaults to 1000 rows × 26 columns; the
"data extent" (`len(sheet.rows)`, `sheet.column_count`) only counts rows/cols
that actually contain cells. Setting the limits low can be used to enforce
size caps; setting them high doesn't materially affect anything.

`tab_color` accepts hex (`"#ff8800"`) or theme color names (`"ACCENT1"`,
`"LINK"`, etc.). Same parser as elsewhere — see [formatting.md](formatting.md).

## Reading cell values in bulk

```python
sheet.values       # list[list[str]] — formatted values, padded to a rectangle
sheet.column_count # int — width of the value rectangle
```

`sheet.values` is the cheapest read path — one `values.get` call returning
just the displayed strings, no formats, no formulas. Useful for "give me the
data, I don't care about styling." Each row is padded with `""` to match the
widest row.

For typed access (numbers as numbers, formulas as `Formula` objects), use
`sheet.cell(r, c).value` — see [cells.md](cells.md).

## Choosing when the fetch happens

```python
sheet.load()   # fetch the grid now, if it hasn't been fetched already
```

Everything loads on demand, so `load()` is never required. It exists because
the order in which you touch a sheet decides how many requests that costs:
`values` / `rows` / `columns` / `column_count` are served by a cheap
`values.get`, while cells, formats and row properties need the full grid.
Reaching for the cheap one first and the rich one afterwards is two round trips
for one sheet:

```python
print(len(sheet.rows))         # values.get
print(sheet.cell(0, 0).value)  # ...and now the grid too — two requests

sheet.load()                   # or: one request, and values come from the grid
print(len(sheet.rows), sheet.cell(0, 0).value)
```

That matters when walking many sheets against a per-minute quota — see
[../rate-limiting.md](../rate-limiting.md).

How much of the sheet that fetch asks for is decided once, when the
spreadsheet is opened: the declared grid, or only the rectangle holding data.
See [`extent`](spreadsheet.md#how-much-of-a-sheet-to-read-extent) — for a
sheet with a wide, mostly-empty grid it is the difference between megabytes
and gigabytes.

## Looking up cells

```python
cell = sheet.cell(row, col)   # 0-based
```

`cell(...)` triggers a grid-data fetch on first call if data wasn't loaded.
Subsequent calls hit the cell cache. Coordinates outside the current data
extent are valid — `sheet.cell(999, 99)` will pad the local cache with empty
cells up to that position. The server-side write only sends what's actually
been modified.

## Rows

```python
sheet.rows           # Rows container
len(sheet.rows)      # number of rows with data
sheet.rows[i]        # → Row
for row in sheet.rows:
    ...              # iterate, allows insert/remove during iteration
sheet.rows.limit     # alias for sheet.max_row_count
```

### `Row` properties and operations

```python
row.index            # int — 0-based, updates after move/remove
row.height           # int — pixel size; get and set
row.hidden           # bool — get and set
row.values           # Sequence[str] — that row's formatted values
row.metadata         # RowDeveloperMetadata — see metadata.md
row.previous_row     # Row | None
row.next_row         # Row | None
row[col]             # Cell at (row.index, col)
len(row)             # column_count

row.remove()         # delete this row; queues a deleteRange request
row.move(before=other_row)  # or after=, or index=
```

After `row.remove()`, any further attribute access on `row` raises
`RuntimeError("This Row has been removed from the sheet")`. Don't reuse
removed Row objects.

### Inserting rows

```python
new = sheet.rows.insert()                # at the end
new = sheet.rows.insert(before=0)        # before index 0
new = sheet.rows.insert(after=sheet.rows[2])  # accepts int or Row
```

The returned `Row` is bound to the new position and can be used immediately
(its cells, metadata, etc. all queue against the right sheet ID).

### Sorting

```python
sheet.rows.sort(key_fn=lambda r: r[0].value, skip_rows=1)
```

`skip_rows` lets you keep a header row in place. The key function gets each
`Row`. Implementation moves rows one-by-one via the API — for big sheets this
is expensive; consider sorting locally and rewriting if you need speed.

## Columns

`Columns` and `Column` mirror `Rows` and `Row` exactly — same operations,
same semantics, just on the other axis.

```python
sheet.columns          # Columns container
len(sheet.columns)     # number of columns with data
sheet.columns[i]       # → Column
for col in sheet.columns:
    ...
sheet.columns.limit    # alias for sheet.max_column_count
sheet.columns.sort(key_fn=lambda c: c.metadata[0].value)

col.index              # int
col.width              # int — pixel size; get and set
col.hidden             # bool — get and set
col.metadata           # ColumnDeveloperMetadata
col.previous_column    # Column | None
col.next_column        # Column | None

col.remove()
col.move(before=other_col)
sheet.columns.insert(before=0)
```

### Index after move

Both `Row.move()` and `Column.move()` accept `before=`, `after=`, or
`index=N`. The `index=N` argument is interpreted as the Sheets API's
`destinationIndex` — i.e. the pre-move position. After the source is removed
and re-inserted, a forward move lands at `index - 1`:

```python
# Sheet: [A, B, C, D].  Move A to index=2.
sheet.rows[0].move(index=2)
# After: [B, A, C, D].  rowA.index == 1 (not 2).
```

For backward moves, `index=N` means exactly that: the row lands at index N.

## Merges

```python
sheet.merge_cells(row0, col0, row1, col1)  # inclusive, both ends
sheet.merge_cells(0, 0, 0, 5)              # merge row 0, cols A through F
```

There's no `unmerge` wrapper yet — use `sheet.spreadsheet._add_request({
"unmergeCells": {"range": {...}}})` if needed.

The list of current merges is in `sheet._merges` (typed `list[gs.GridRange]`).
Not currently exposed as a public property.

## Snapshot reference

```python
sheet.spreadsheet.snapshot()           # → SpreadsheetSnapshot dict
sheet.spreadsheet.save_snapshot(path)  # writes JSON to disk
```

Snapshots are spreadsheet-level (not per-sheet). See
[snapshot.md](snapshot.md).

## What's deliberately missing

`Sheet.__init__` silently drops several feature lists from the API response:
`conditionalFormats`, `filterViews`, `basicFilter`, `charts`, `bandedRanges`,
`rowGroups`, `columnGroups`, `slicers`. These aren't modeled. If you need
them, drop down to `sheet.spreadsheet._service.resource` for raw API access.

## See also

- [cells.md](cells.md) — `Cell`, value types, hyperlinks
- [formatting.md](formatting.md) — `CellFormat`, `BorderFormat`
- [metadata.md](metadata.md) — `DeveloperMetadata` on rows, columns, and sheets
- [../rate-limiting.md](../rate-limiting.md) — `Sheet.load()` and the per-minute
  quota it exists to save
