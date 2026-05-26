# `CellFormat` and `BorderFormat`

Cell formatting: number formats, colors, alignment, fonts, borders. All of
it lives on `cell.format`.

## Reading vs writing

```python
fmt = cell.format          # CellFormat wrapper

# Reading: just attribute access.
fmt.is_bold                # True
fmt.background_color       # "#ffeebb" or None
fmt.number_format          # "CURRENCY($#,##0.00)"

# Writing: assignment queues an updateCells request.
fmt.is_bold = True
fmt.background_color = "#ff8800"
fmt.foreground_color = "ACCENT1"
fmt.padding = (4, 8, 4, 8)            # top, right, bottom, left
fmt.horizontal_alignment = "CENTER"
fmt.vertical_alignment = "MIDDLE"
fmt.wrap_strategy = "WRAP"
fmt.font_family = "Roboto Mono"
fmt.font_size = 12
fmt.number_format = "PERCENT(0.00%)"
fmt.is_italic = True
fmt.is_strikethrough = True
fmt.is_underline = True
```

Each setter short-circuits if the new value equals the current one
(`if value == self.foo: return`), so idempotent writes are free.

The whole format can be assigned at once:

```python
cell.format = other_cell.format   # copy from another cell
cell.format = None                # reset to spreadsheet default
```

## Colors

Three accepted forms for any color setter:

```python
fmt.background_color = "#ff8800"     # hex, 7 chars (RGB)
fmt.background_color = "#ff8800cc"   # hex, 9 chars (RGBA, alpha in last 2)
fmt.background_color = "ACCENT1"     # theme color name
fmt.background_color = None          # clear
```

Theme color names: `TEXT`, `BACKGROUND`, `LINK`, `ACCENT1` through `ACCENT6`.
These resolve to whatever the spreadsheet's theme defines for that role. Use
theme names to stay consistent if the spreadsheet's theme changes.

Reads return the same canonical form: hex if the cell uses a literal RGBA
color, theme name if it uses a themed one, `None` if unset.

## Number formats

Number formats are encoded as a string `"TYPE"` or `"TYPE(pattern)"`:

```python
fmt.number_format = "TEXT"
fmt.number_format = "NUMBER"
fmt.number_format = "NUMBER(#,##0.00)"
fmt.number_format = "PERCENT(0.00%)"
fmt.number_format = "CURRENCY($#,##0.00)"
fmt.number_format = "DATE(yyyy-mm-dd)"
fmt.number_format = "TIME(h:mm:ss am/pm)"
fmt.number_format = "DATE_TIME(yyyy-mm-dd h:mm:ss)"
fmt.number_format = "SCIENTIFIC(0.00E+00)"
```

Valid types: `TEXT`, `NUMBER`, `PERCENT`, `CURRENCY`, `DATE`, `TIME`,
`DATE_TIME`, `SCIENTIFIC`.

The pattern syntax is exactly what Sheets accepts — see Google's [number
format pattern docs](https://developers.google.com/sheets/api/guides/formats).
The library doesn't validate patterns (just the type). A bad pattern lands
on the server and the API may reject it on `save()`.

Reading: `fmt.number_format` returns the round-trip-compatible string.
If the cell has no number format set, returns `"TEXT"`.

## Alignment and wrap

```python
fmt.horizontal_alignment   # Literal["LEFT", "CENTER", "RIGHT"] | None
fmt.vertical_alignment     # Literal["TOP", "MIDDLE", "BOTTOM"] | None
fmt.wrap_strategy          # Literal["OVERFLOW_CELL", "LEGACY_WRAP", "CLIP", "WRAP"] | None
```

`None` on read means "unset; inherits spreadsheet default."

## Fonts

```python
fmt.font_family   # str | None
fmt.font_size     # float  (defaults to 10 if unset)
fmt.is_bold       # bool   (defaults to False)
fmt.is_italic     # bool
fmt.is_underline  # bool
fmt.is_strikethrough  # bool
```

`font_family` accepts any string Google's font picker would. Common values:
`"Arial"`, `"Roboto"`, `"Roboto Mono"`, `"Calibri"`, `"Times New Roman"`.

Boolean flags default to False when reading; assigning `False` explicitly is
a no-op if the cell didn't have the flag set.

## Padding

```python
fmt.padding   # tuple[float, float, float, float] | None — (top, right, bottom, left)
fmt.padding = (4, 8, 4, 8)
fmt.padding = None        # reset
```

Setting `None` queues a `padding: {}` clear. Components default to 0 if any
edge is missing in the server response.

## Borders

The four edge properties:

```python
fmt.border_top      # BorderFormat | None
fmt.border_right    # BorderFormat | None
fmt.border_bottom   # BorderFormat | None
fmt.border_left     # BorderFormat | None
```

Each returns a `BorderFormat` instance or `None` for "no border."

```python
from gservices.sheets.border_format import BorderFormat

fmt.border_bottom = BorderFormat(style="SOLID", width=1, color="#000000")
fmt.border_right = BorderFormat(style="DASHED", width=2)
fmt.border_top = None   # clear (collapses to width=0, style=None on read)
```

### `BorderFormat`

```python
BorderFormat(
    style="SOLID",       # DOTTED | DASHED | SOLID | SOLID_MEDIUM | SOLID_THICK | DOUBLE | None
    width=1,             # pixels
    color="#000000",     # hex or theme name; None for "no color"
)
```

A `BorderFormat` with `width=0` is invisible. The constructor normalizes
this case: `width=0` forces `style=None` and `color=None`, so an "empty"
border has consistent identity.

`BorderFormat` supports `==` comparison (across instances, against `dict`
shaped like a `gs.Border`, etc.) — used by the setter to short-circuit
no-op writes.

## Print / debug

```python
fmt.print()           # dumps every attribute to stdout
fmt.print(indent="  ")
```

Used by `cell.print()` for nested output.

## What lives where

Internally `CellFormat` wraps a `gs.CellFormat` TypedDict from the API. The
wrapper splits things between two surfaces:

- `userEnteredFormat` — what the user (or your code) set. This is what
  `_set_property` writes via the `updateCells` request.
- `effectiveFormat` — the resolved formatting after default inheritance.
  This is what the wrapper *reads* from (so unset attributes return the
  spreadsheet default, not `None`).

So writing to `fmt.is_bold = True` sets `userEnteredFormat.textFormat.bold`,
and the next `save()` + reload propagates that to `effectiveFormat`.

You generally don't need to think about this distinction.

## See also

- [cells.md](cells.md) — `Cell.format` access path
- The Sheets API [number format pattern docs](https://developers.google.com/sheets/api/guides/formats) for `number_format` patterns
