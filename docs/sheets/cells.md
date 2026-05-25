# `Cell`

One cell at a fixed `(row, column)` position in a sheet. Lazy-instantiated
via `sheet.cell(r, c)`.

## Access

```python
cell = sheet.cell(0, 0)         # 0-based, always works
cell = sheet.rows[0][0]         # equivalent
```

`sheet.cell()` is cached: two calls with the same coordinates return the
same `Cell` object. Mutating one is visible to the other (they share `_data`).

## Address and URL

```python
cell.row              # 0
cell.column           # 0
cell.name             # "A1" (Excel-style address)
cell.url              # "https://docs.google.com/spreadsheets/d/.../edit#gid=0&range=A1"
```

`cell.url` opens the cell directly in the Sheets UI — handy for emailing
links to specific cells.

## Three flavors of value

Google Sheets distinguishes three different per-cell strings/values, and the
library exposes all of them:

| Property | Type | What it is |
|---|---|---|
| `cell.user_entered_value` | typed union | What was *typed* (or written via the API). Formula text for formula cells. |
| `cell.effective_value` | typed union | The *computed* result. Formula → number/string output. |
| `cell.formatted_value` | `str` | The *rendered* text shown in the UI (after number-format / date-format). |

Type of the first two: `str | float | bool | Formula | ErrorValue | None`.

```python
# Cell with =SUM(A1:A10) entered by the user, format "$#,##0.00"
cell.user_entered_value  # Formula(text="=SUM(A1:A10)")
cell.effective_value     # 1234.56  (float)
cell.formatted_value     # "$1,234.56"  (str)

# Cell with the literal number 42, no special format
cell.user_entered_value  # 42  (int/float)
cell.effective_value     # 42
cell.formatted_value     # "42"

# Cell with an error formula =1/0, format default
cell.user_entered_value  # Formula(text="=1/0")
cell.effective_value     # ErrorValue(type="DIVIDE_BY_ZERO", message="...")
cell.formatted_value     # "#DIV/0!"

# Empty cell
cell.user_entered_value  # None
cell.effective_value     # None
cell.formatted_value     # ""
```

`cell.value` is an alias for `cell.effective_value` (the most common access
path). Use `user_entered_value` when you need to distinguish "literal 5" from
"=COUNTA(A:A) that happens to equal 5".

## Writing values

```python
cell.value = "hello"                 # str
cell.value = 42                      # int / float
cell.value = True                    # bool
cell.value = None                    # clear the cell
cell.value = ""                      # also clears (normalized to None)

cell.value = Formula("=A1+1")        # formula

cell.value = HyperlinkFormula(
    url="https://example.com",
    label="Visit",
)                                    # =HYPERLINK("...", "Visit")
```

Each assignment queues an `updateCells` request with `userEnteredValue` and
flushes on `save()`.

### What gets cached locally after a write

For literals (str, int, float, bool, None), the local `cell` reflects the
new value immediately:

```python
cell.value = 42
cell.effective_value   # 42
cell.formatted_value   # "42"
```

For formulas, the formula text is stored but the computed result is
unknown locally (the spreadsheet evaluates server-side). The library
invalidates `effective_value` and `formatted_value` so they don't lie:

```python
cell.value = Formula("=2+2")
cell.user_entered_value  # Formula("=2+2")
cell.effective_value     # None  ← honest: we don't know yet
cell.formatted_value     # ""    ← same
# After spreadsheet.save() and a re-fetch (or a fresh open):
cell.effective_value     # 4
cell.formatted_value     # "4"
```

If you want the formula result immediately, the only path is to `save()` and
re-open the spreadsheet (or call internal `_load_data` on the sheet again).

### Skipping no-op writes

The setter short-circuits when the new value equals the current value. For
formulas, equality is by formula text. For `HyperlinkFormula` specifically,
equality also checks that the cell's current hyperlink and formatted text
match — useful when you're idempotently re-asserting the same link.

## Notes

```python
cell.note          # str
cell.note = "Reviewed by Alice"
cell.note = ""     # clear
```

## Hyperlinks

```python
cell.hyperlink     # str | None — read-only
```

Cells can carry hyperlinks via two paths:
1. A `=HYPERLINK(url, label)` formula in `user_entered_value`.
2. A "rich-link" attached to the cell directly (typed-paste from a web URL,
   intra-spreadsheet link via `#gid=`, etc.).

`cell.hyperlink` reflects whichever applies. To set a hyperlink, assign a
`HyperlinkFormula` to `cell.value`:

```python
cell.value = HyperlinkFormula("https://example.com", "click")
```

For rich-link types (no formula text), there's no public setter in this
library yet — drop to raw API.

## Formula objects

```python
from gservices.sheets.cell_value import Formula, HyperlinkFormula

Formula("=A1+1")             # generic formula
HyperlinkFormula(url, label) # builds =HYPERLINK(...) with escaped label
```

`Formula` is a thin wrapper around the formula string. `Formula.text` gives
you the raw `"=..."`.

`HyperlinkFormula` is a `Formula` subclass that builds the formula text for
you and remembers `url` / `label` for later identity checks. Construction
also normalizes the URL (auto-prepends `http://` if no scheme).

## Error values

When a formula evaluates to an error, `effective_value` returns:

```python
@dataclass
class ErrorValue:
    message: str
    type: Literal[
        "ERROR", "NULL_VALUE", "DIVIDE_BY_ZERO", "VALUE",
        "REF", "NAME", "NUM", "N_A", "LOADING",
    ]
```

The `message` is the human-readable text from Sheets ("Division by zero",
etc.). The `type` is the error code; map to the `#DIV/0!`-style display
yourself if you need it.

## Format access

```python
cell.format          # CellFormat — see formatting.md
cell.format = other_cell.format   # copy formatting from another cell
cell.format = None   # reset to spreadsheet default
```

The getter returns a wrapper bound to this cell — mutations on
`cell.format.is_bold = True` queue update requests directly.

## Debug aid

```python
cell.print()  # human-readable dump to stdout
```

Useful when poking at unfamiliar cells in a REPL.

## See also

- [formatting.md](formatting.md) — `CellFormat`, colors, fonts, borders
- [sheets.md](sheets.md) — `Sheet`, rows, columns
