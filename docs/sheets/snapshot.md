# Snapshots

A spreadsheet snapshot is a structured JSON representation of the entire
spreadsheet, optimized for `git diff` readability and for "what changed
between two points in time" workflows.

The format is **layered**, mirroring how a human reads a spreadsheet:
data first, then merges, then formulas, then formats, then borders.
Changing a cell value diffs `data`; restyling a column diffs `formats`;
adding a border diffs `borders`. The layers don't pollute each other.

## Quick start

```python
ss = google.Sheets.open(file_id)

# Get the snapshot as a dict.
snap = ss.snapshot()

# Or write directly to disk.
ss.save_snapshot("spreadsheet.snap.json")

# Commit the file to git. Re-run later; `git diff` shows exactly what changed.
```

`save_snapshot` writes JSON with one data row per line, format entries on
their own lines, borders grouped by line segment — all designed to make
`git diff` output focus on the actual changes, not whitespace noise.

## When to use it

- **Regression tracking.** Periodic snapshots committed to a repo give you
  a `git log` of how a spreadsheet evolves. Diffs are readable enough that
  code review tooling works on them.
- **Change auditing.** Compare two snapshots to see exactly what mutated
  between runs of a pipeline.
- **Backups.** A snapshot captures enough state to reconstruct most of a
  spreadsheet (though it isn't currently reversible — see
  [limitations](#limitations)).

## Format overview

```json
{
  "schema_version": 1,
  "spreadsheet": {
    "id": "1abc...XYZ",
    "title": "Quarterly Report",
    "locale": "en_US",
    "time_zone": "America/New_York",
    "theme": {"font_family": "Arial", "colors": {...}}
  },
  "sheets": [
    {
      "title": "Summary",
      "sheet_id": 0,
      "grid": {"rows": 1000, "cols": 26},
      "frozen": {"rows": 1},
      "data": [
        ["Name",  "Date",       "Activity"],
        ["John",  "2026-04-01", null],
        ["Jane",  "2026-04-02", "Meeting"],
        ["Alice", "2026-04-03", "=B4+1"]
      ],
      "merges":   ["A1:C1"],
      "formulas": ["C4"],
      "formats": [
        {"range": "1:1",   "fmt": {"bold": true, "bg": "#EEEEEE", "halign": "CENTER"}},
        {"range": "B:B",   "fmt": {"halign": "RIGHT", "number_format": "DATE(yyyy-mm-dd)"}},
        {"range": "A2",    "fmt": {"strikethrough": true}}
      ],
      "borders": {
        "horizontal": [["A1:C1", {"style": "SOLID", "color": "#000000"}]],
        "vertical":   []
      },
      "rows":    {"0": {"height": 32}},
      "columns": {"A": {"width": 180}},
      "notes":   {"C2": "Cancelled"},
      "hyperlinks": {"A4": "#gid=1138"}
    }
  ]
}
```

The layers, in order:

| Layer | Key | Contents |
|---|---|---|
| 1 | `data` | 2D array of values |
| 1.5 | `merges` | A1 ranges of merged regions |
| 1.6 | `formulas` | A1 ranges marking which `data` entries are formulas |
| 2 | `formats` | Format-keyed groups: `{range, fmt}` entries |
| 3 | `borders` | Horizontal/vertical line segments |
| 4 | `rows`, `columns` | Per-dimension overrides (height, width, hidden, metadata) |
| 5 | `notes`, `hyperlinks`, `computed` | Sparse per-cell side maps |
| 6 | `metadata`, `protected_ranges` | Developer metadata, protections |

## Value encoding

Cell values in `data` are bare scalars when possible. Type information lives
in the side layers:

- **Empty cell** → `null`
- **String** → JSON string
- **Number** → JSON number
- **Boolean** → JSON bool
- **Date / time / datetime** → ISO 8601 string (`"2026-04-01"`, `"14:30:00"`,
  `"2026-04-01T14:30:00"`). The `formats` layer's `number_format` marks the
  cell as a date — without that, it's just a plain string. **Never** stored as
  serial-number floats (which would be unreadable and would diff noisily on
  pure format changes).
- **Formula** → the formula text as a string (e.g. `"=A1+1"`). The cell's
  A1 address is also recorded in the `formulas` side-list, which is what
  *makes* it a formula (vs. a literal string starting with `=`).
- **Error** → `{"error": "REF", "message": "..."}` tagged dict. Errors are
  rare enough that the tagged form doesn't hurt readability.

## Ambiguity: literal strings starting with `=`

If a cell literally contains the string `"=foo"` (not a formula — just a
string that happens to start with `=`), the snapshot stores it as `"=foo"`
in `data` but does NOT list its address in `formulas`. So `formulas` is
load-bearing for disambiguation:

- In `formulas` → it's a formula.
- Not in `formulas` → it's a string literal.

## Format grouping

The `formats` layer groups cells by their format fingerprint, then compacts
each group into A1 ranges using a greedy heuristic:

1. **Full columns** first: if every populated row in column C carries this
   format, emit `C:C`.
2. **Full rows** next: same for rows.
3. **Maximal rectangles**: greedy left-to-right, top-to-bottom rectangle
   growth on what's left.
4. **Singletons**: comma-joined cell addresses for stragglers.

The range strings the layer emits:
- Single cell: `B5`
- Rectangle: `A1:C5`
- Full column: `B:B`
- Full row: `5:5`
- Union: `A1:B3,C5,D:D`

Same compaction is used for `formulas` (one A1 range list marking which
data entries are formulas). For a typical column of identical-shape
formulas, this collapses to one entry like `"B2:B100"` — diffs stay
single-line even when 99 formula cells exist.

## Borders

Border edges canonicalize to a cell's bottom or right side. The cell at
`(r, c)` having a `top` border is encoded as the cell at `(r-1, c)` having
a `bottom` border. Same for `left` → `right` on `(r, c-1)`. Borders on the
outermost edge of the grid (top of row 0, left of column A) are dropped —
they're outside the renderable area.

Border edges are then grouped by `(direction, style+color+width)` and
compacted into line segments:

```json
"borders": {
  "horizontal": [
    ["A1:Z1", {"style": "SOLID", "color": "#000000", "width": 1}]
  ],
  "vertical": [
    ["B1:B100", {"style": "SOLID_THICK"}]
  ]
}
```

## `include_computed` — formula results

By default, `data` contains only literal values and formula text. To also
capture the computed *result* of each formula:

```python
ss.save_snapshot("snap.json", include_computed=True)
```

This adds a `computed` map per sheet:

```json
"computed": {
  "C4": 5,
  "D7": "2026-05-25"
}
```

**Default is off** for a reason: volatile formulas (`NOW`, `TODAY`,
`RAND*`, `IMPORT*`, `GOOGLEFINANCE`) produce spurious diffs on every
snapshot. External-reference formulas drift when upstream sheets change.
For the "what did *I* change?" use case, computed values are noise.

Turn it on selectively when you specifically want to capture results.

## Stability invariants

Two consecutive `save_snapshot()` calls produce byte-identical output if
the spreadsheet hasn't changed:

```python
ss.save_snapshot("s1.json")
ss.save_snapshot("s2.json")
# diff s1.json s2.json  →  no output
```

This includes:
- Sheets in `index` order
- Data rows in row-index order
- Format entries sorted by first range component
- Format attribute keys in fixed order
- Developer metadata sorted by `(key, value)` (server-assigned IDs are
  omitted from the snapshot so re-imports don't produce ID-shuffle diffs)

## Layered independence

Round-trip independence is the main correctness invariant:

- Changing a cell value diffs `data` only (not `formats` or `borders`).
- Adding a border diffs `borders` only.
- Toggling bold on a column diffs `formats` only.
- Inserting a row shifts subsequent `data` rows by one but doesn't touch
  unrelated layers.

So a typical `git diff` output zeroes in on what the human cares about.

## Performance

For large spreadsheets, snapshot building is dominated by the API fetch
and the format-fingerprint compaction. Key optimizations already applied
(see PR series #5–#10):

- One batch `spreadsheets.get` for all sheets, with a `fields=` mask
  restricting the response to just the data the snapshot reads.
- orjson for JSON deserialization (rust-based, ~3x faster than stdlib).
- Tuple-of-sorted-items fingerprints for format grouping (vs. `json.dumps`).
- Per-`(r,g,b,a)` color memoization.
- Precomputed spreadsheet-default-format derivations.

For a 30-sheet spreadsheet (~70 MB API response), a snapshot takes ~25
seconds end to end. Most of that is the API fetch.

## Limitations

- **No loader.** `save_snapshot` is one-way. You can't read a snapshot back
  into a `Spreadsheet` object. The JSON is self-describing enough to parse
  manually, but reconstructing live Spreadsheet state isn't built in.
- **No "apply" mode.** Can't take an edited snapshot and push the diffs back
  to the live spreadsheet.
- **Single file output.** Everything goes into one JSON. For very large
  spreadsheets (10MB+ snapshots) this can be unwieldy — no per-sheet split.
- **Charts, pivot tables, data validation, conditional formatting, filter
  views, banded ranges, slicers** are not captured (the underlying library
  doesn't model them).

## See also

- [spreadsheet.md](spreadsheet.md) — `ss.snapshot()` / `ss.save_snapshot(path)`
- [formatting.md](formatting.md) — what's in the `formats` and `borders` layers
