"""
Layered JSON snapshot of a Google Sheets spreadsheet.

The output is structured to mirror how a human reads a spreadsheet — data first,
then merges, formulas, formats, borders, dimensions, and finally side-channel
maps (notes, hyperlinks, computed values, developer metadata). `git diff` of
two snapshots highlights only the cells / properties that actually changed.

Public API:
- `build_snapshot(spreadsheet)` -> SpreadsheetSnapshot (a JSON-shaped dict)
- `write_snapshot(snap, path)` writes the snapshot to disk with diff-friendly layout

See SpreadsheetSnapshot for the schema.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast

from gservices.sheets.utils import color_object_to_string, coords_to_address

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

    from gservices.sheets.sheet import Sheet
    from gservices.sheets.spreadsheet import Spreadsheet


SCHEMA_VERSION = 1

_DEFAULT_ROW_HEIGHT = 21
_DEFAULT_COL_WIDTH = 100
_DATE_EPOCH = datetime(1899, 12, 30)  # Google Sheets / Excel serial-date epoch.

_DATE_FORMAT_TYPES = ("DATE", "TIME", "DATE_TIME")

_CELL_FORMAT_KEY_ORDER = (
    "number_format",
    "bg",
    "fg",
    "padding",
    "halign",
    "valign",
    "wrap",
    "font_family",
    "font_size",
    "bold",
    "italic",
    "underline",
    "strikethrough",
)

_SHEET_KEY_ORDER = (
    "title",
    "sheet_id",
    "type",
    "hidden",
    "tab_color",
    "grid",
    "frozen",
    "hide_gridlines",
    "data",
    "merges",
    "formulas",
    "formats",
    "borders",
    "rows",
    "columns",
    "notes",
    "hyperlinks",
    "computed",
    "metadata",
)

_SPREADSHEET_META_KEY_ORDER = (
    "id",
    "title",
    "locale",
    "time_zone",
    "theme",
    "default_cell_format",
    "metadata",
)


# ============================================================================
# TypedDicts
# ============================================================================

class ErrorValueJSON(TypedDict):
    error: str
    message: NotRequired[str]


CellValueJSON = str | int | float | bool | None | ErrorValueJSON


class BorderSnapshot(TypedDict, total=False):
    style: str
    width: int
    color: str


class CellFormatSnapshot(TypedDict, total=False):
    number_format: str
    bg: str
    fg: str
    padding: list[float]
    halign: Literal["LEFT", "CENTER", "RIGHT"]
    valign: Literal["TOP", "MIDDLE", "BOTTOM"]
    wrap: Literal["OVERFLOW_CELL", "LEGACY_WRAP", "CLIP", "WRAP"]
    font_family: str
    font_size: float
    bold: bool
    italic: bool
    underline: bool
    strikethrough: bool


class FormatEntrySnapshot(TypedDict):
    range: str
    fmt: CellFormatSnapshot


class BordersSnapshot(TypedDict, total=False):
    horizontal: list[list[Any]]  # list of [range_str, BorderSnapshot]
    vertical: list[list[Any]]


class MetadataSnapshot(TypedDict):
    key: str
    value: str
    public: bool


class RowMetaSnapshot(TypedDict, total=False):
    height: int
    hidden: bool
    metadata: list[MetadataSnapshot]


class ColMetaSnapshot(TypedDict, total=False):
    width: int
    hidden: bool
    metadata: list[MetadataSnapshot]


class FrozenSnapshot(TypedDict, total=False):
    rows: int
    cols: int


class GridDimsSnapshot(TypedDict):
    rows: int
    cols: int


class ThemeSnapshot(TypedDict, total=False):
    font_family: str
    colors: dict[str, str]


class SpreadsheetMetaSnapshot(TypedDict, total=False):
    id: str
    title: str
    locale: str
    time_zone: str
    theme: ThemeSnapshot
    default_cell_format: CellFormatSnapshot
    metadata: list[MetadataSnapshot]


class SheetSnapshot(TypedDict, total=False):
    title: str
    sheet_id: int
    type: Literal["GRID", "OBJECT", "DATA_SOURCE"]
    hidden: bool
    tab_color: str
    grid: GridDimsSnapshot
    frozen: FrozenSnapshot
    hide_gridlines: bool
    data: list[list[CellValueJSON]]
    merges: list[str]
    formulas: list[str]
    formats: list[FormatEntrySnapshot]
    borders: BordersSnapshot
    rows: dict[str, RowMetaSnapshot]
    columns: dict[str, ColMetaSnapshot]
    notes: dict[str, str]
    hyperlinks: dict[str, str]
    computed: dict[str, CellValueJSON]
    metadata: list[MetadataSnapshot]


class SpreadsheetSnapshot(TypedDict):
    schema_version: int
    spreadsheet: SpreadsheetMetaSnapshot
    sheets: list[SheetSnapshot]


# ============================================================================
# Public API
# ============================================================================

def build_snapshot(
    spreadsheet: "Spreadsheet",
    include_computed: bool = False,
) -> SpreadsheetSnapshot:
    """
    Builds an in-memory snapshot of the spreadsheet's current state.

    Triggers a full grid load on each sheet if cell data hasn't been loaded yet.
    """
    sheet_snaps = [
        _build_sheet(sheet, spreadsheet, include_computed)
        for sheet in spreadsheet.sheets
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "spreadsheet": _build_spreadsheet_meta(spreadsheet),
        "sheets": sheet_snaps,
    }


def write_snapshot(snap: SpreadsheetSnapshot, path: "str | Path") -> None:
    """Writes a snapshot to disk as JSON with diff-friendly layout."""
    wrapped = _wrap_for_emit(snap)
    text = _emit(wrapped, depth=0) + "\n"
    Path(path).write_text(text, encoding="utf-8")


# ============================================================================
# Builders
# ============================================================================

def _build_spreadsheet_meta(
    spreadsheet: "Spreadsheet",
) -> SpreadsheetMetaSnapshot:
    result: dict[str, Any] = {
        "id": spreadsheet.id,
        "title": spreadsheet.title,
        "locale": spreadsheet.locale,
        "time_zone": spreadsheet.time_zone,
    }
    theme = _build_theme(spreadsheet.theme)
    if theme:
        result["theme"] = theme
    default_format_data = cast(
        "gs.CellFormat",
        spreadsheet._properties.get("defaultFormat", {}),  # type: ignore[reportPrivateUsage]
    )
    fmt = _extract_cell_format(default_format_data, {})
    if fmt:
        result["default_cell_format"] = fmt
    metadata = _build_metadata_items(_spreadsheet_metadata_raw(spreadsheet))
    if metadata:
        result["metadata"] = metadata
    return cast(
        SpreadsheetMetaSnapshot,
        _order_keys(result, _SPREADSHEET_META_KEY_ORDER),
    )


def _build_theme(theme: "gs.SpreadsheetTheme") -> ThemeSnapshot:
    result: dict[str, Any] = {}
    ff = theme.get("primaryFontFamily")
    if ff:
        result["font_family"] = ff
    colors: dict[str, str] = {}
    for entry in theme.get("themeColors", []):
        ctype = entry.get("colorType")
        if not ctype or ctype == "THEME_COLOR_TYPE_UNSPECIFIED":
            continue
        color = color_object_to_string(entry.get("color"))
        if color:
            colors[ctype] = color
    if colors:
        # Stable theme color order matching Google's conventional ordering.
        theme_order = (
            "TEXT",
            "BACKGROUND",
            "LINK",
            "ACCENT1",
            "ACCENT2",
            "ACCENT3",
            "ACCENT4",
            "ACCENT5",
            "ACCENT6",
        )
        ordered_colors = {k: colors[k] for k in theme_order if k in colors}
        for k in sorted(colors):
            if k not in ordered_colors:
                ordered_colors[k] = colors[k]
        result["colors"] = ordered_colors
    return cast(ThemeSnapshot, result)


def _build_sheet(
    sheet: "Sheet",
    spreadsheet: "Spreadsheet",
    include_computed: bool,
) -> SheetSnapshot:
    sheet._load_data()  # type: ignore[reportPrivateUsage]

    result: dict[str, Any] = {
        "title": sheet.title,
        "sheet_id": sheet.id,
    }
    if sheet.type != "GRID":
        result["type"] = sheet.type
    if sheet.hidden:
        result["hidden"] = True
    if sheet.tab_color:
        result["tab_color"] = sheet.tab_color

    result["grid"] = {
        "rows": sheet.max_row_count,
        "cols": sheet.max_column_count,
    }

    frozen: dict[str, int] = {}
    if sheet.frozen_row_count:
        frozen["rows"] = sheet.frozen_row_count
    if sheet.frozen_column_count:
        frozen["cols"] = sheet.frozen_column_count
    if frozen:
        result["frozen"] = frozen

    if sheet.hide_gridlines:
        result["hide_gridlines"] = True

    # Walk cell grid and collect all layers.
    default_format = cast(
        "gs.CellFormat",
        spreadsheet._properties.get("defaultFormat", {}),  # type: ignore[reportPrivateUsage]
    )
    cell_data: "gs.GridData | None" = sheet._cell_data  # type: ignore[reportPrivateUsage]
    rows_data: list[Any] = (
        cell_data.get("rowData", []) if cell_data is not None else []
    )

    cell_values: dict[tuple[int, int], CellValueJSON] = {}
    formula_cells: set[tuple[int, int]] = set()
    format_groups: dict[str, tuple[CellFormatSnapshot, set[tuple[int, int]]]] = {}
    border_edges: dict[
        tuple[str, str, int, str | None], set[tuple[int, int]]
    ] = {}
    notes: dict[tuple[int, int], str] = {}
    hyperlinks: dict[tuple[int, int], str] = {}
    computed: dict[tuple[int, int], CellValueJSON] = {}

    max_row_seen = 0
    max_col_seen = 0

    for ri, row in enumerate(rows_data):
        row_obj = cast("gs.RowData", row)
        values = row_obj.get("values", [])
        for ci, cv_any in enumerate(values):
            cv = cast("gs.CellData", cv_any)
            has_content = False

            uev = cv.get("userEnteredValue")
            eff_fmt = cv.get("effectiveFormat", {})
            nf = eff_fmt.get("numberFormat")
            nf_type = nf.get("type") if nf else None
            if nf_type == "NUMBER_FORMAT_TYPE_UNSPECIFIED":
                nf_type = None

            encoded = _encode_cell_value(uev, nf_type)
            if encoded is not None:
                cell_values[(ri, ci)] = encoded
                has_content = True

            if uev is not None and uev.get("formulaValue") is not None:
                formula_cells.add((ri, ci))

            fmt_snap = _extract_cell_format(eff_fmt, default_format)
            if fmt_snap:
                key = json.dumps(fmt_snap, sort_keys=True, ensure_ascii=False)
                slot = format_groups.get(key)
                if slot is None:
                    format_groups[key] = (fmt_snap, {(ri, ci)})
                else:
                    slot[1].add((ri, ci))
                has_content = True

            borders = eff_fmt.get("borders")
            if borders:
                if _collect_borders(borders, ri, ci, border_edges):
                    has_content = True

            note = cv.get("note")
            if note:
                notes[(ri, ci)] = note
                has_content = True

            hyperlink = cv.get("hyperlink")
            if hyperlink:
                hyperlinks[(ri, ci)] = hyperlink
                has_content = True

            if include_computed and (ri, ci) in formula_cells:
                ev = cv.get("effectiveValue")
                if ev is not None:
                    cv_encoded = _encode_cell_value(ev, nf_type)
                    if cv_encoded is not None:
                        computed[(ri, ci)] = cv_encoded

            if has_content:
                if ri >= max_row_seen:
                    max_row_seen = ri + 1
                if ci >= max_col_seen:
                    max_col_seen = ci + 1

    data_rows = max_row_seen
    data_cols = max_col_seen

    if data_rows > 0 and data_cols > 0:
        data_array: list[list[CellValueJSON]] = []
        for ri in range(data_rows):
            row_arr: list[CellValueJSON] = []
            for ci in range(data_cols):
                row_arr.append(cell_values.get((ri, ci)))
            data_array.append(row_arr)
        result["data"] = data_array

    merges = _build_merges(_sheet_merges_raw(sheet))
    if merges:
        result["merges"] = merges

    if formula_cells:
        result["formulas"] = _compact_to_range_list(
            formula_cells, data_rows, data_cols
        )

    if format_groups:
        formats_list: list[FormatEntrySnapshot] = []
        for fmt_snap, cells in format_groups.values():
            range_str = _compact_cells_to_ranges(cells, data_rows, data_cols)
            formats_list.append({"range": range_str, "fmt": fmt_snap})
        formats_list.sort(key=lambda e: _range_sort_key(e["range"]))
        result["formats"] = formats_list

    borders_snap = _build_borders(border_edges, data_rows, data_cols)
    if borders_snap:
        result["borders"] = borders_snap

    if cell_data is not None:
        row_meta_list = cast(list[Any], cell_data.get("rowMetadata", []))
        col_meta_list = cast(list[Any], cell_data.get("columnMetadata", []))
        rows_dict = _build_row_meta(row_meta_list)
        cols_dict = _build_col_meta(col_meta_list)
        if rows_dict:
            result["rows"] = rows_dict
        if cols_dict:
            result["columns"] = cols_dict

    if notes:
        result["notes"] = _addr_dict(notes)
    if hyperlinks:
        result["hyperlinks"] = _addr_dict(hyperlinks)
    if computed:
        result["computed"] = _addr_dict(computed)

    sheet_metadata = _build_metadata_items(_sheet_metadata_raw(sheet))
    if sheet_metadata:
        result["metadata"] = sheet_metadata

    return cast(SheetSnapshot, _order_keys(result, _SHEET_KEY_ORDER))


def _build_merges(merges: list["gs.GridRange"]) -> list[str]:
    out: list[str] = []
    for m in merges:
        r0 = m.get("startRowIndex", 0)
        r1 = m.get("endRowIndex", 0)
        c0 = m.get("startColumnIndex", 0)
        c1 = m.get("endColumnIndex", 0)
        if r1 <= r0 or c1 <= c0:
            continue
        if r0 == r1 - 1 and c0 == c1 - 1:
            out.append(coords_to_address(r0, c0))
        else:
            out.append(
                f"{coords_to_address(r0, c0)}:{coords_to_address(r1 - 1, c1 - 1)}"
            )
    out.sort(key=_range_sort_key)
    return out


def _build_row_meta(
    row_meta_list: list["gs.DimensionProperties"],
) -> dict[str, RowMetaSnapshot]:
    out: dict[str, RowMetaSnapshot] = {}
    for i, rm in enumerate(row_meta_list):
        entry: dict[str, Any] = {}
        height = rm.get("pixelSize")
        if height is not None and height != _DEFAULT_ROW_HEIGHT:
            entry["height"] = height
        if rm.get("hiddenByUser"):
            entry["hidden"] = True
        meta = _build_metadata_items(rm.get("developerMetadata", []))
        if meta:
            entry["metadata"] = meta
        if entry:
            out[str(i)] = cast(RowMetaSnapshot, entry)
    return out


def _build_col_meta(
    col_meta_list: list["gs.DimensionProperties"],
) -> dict[str, ColMetaSnapshot]:
    out: dict[str, ColMetaSnapshot] = {}
    for i, cm in enumerate(col_meta_list):
        entry: dict[str, Any] = {}
        width = cm.get("pixelSize")
        if width is not None and width != _DEFAULT_COL_WIDTH:
            entry["width"] = width
        if cm.get("hiddenByUser"):
            entry["hidden"] = True
        meta = _build_metadata_items(cm.get("developerMetadata", []))
        if meta:
            entry["metadata"] = meta
        if entry:
            out[_col_to_letter(i)] = cast(ColMetaSnapshot, entry)
    return out


def _build_metadata_items(
    data_list: list["gs.DeveloperMetadata"],
) -> list[MetadataSnapshot]:
    items: list[MetadataSnapshot] = []
    for item in data_list:
        items.append({
            "key": item.get("metadataKey", ""),
            "value": item.get("metadataValue", ""),
            "public": item.get("visibility") == "DOCUMENT",
        })
    items.sort(key=lambda m: (m["key"], m["value"]))
    return items


def _spreadsheet_metadata_raw(
    spreadsheet: "Spreadsheet",
) -> list["gs.DeveloperMetadata"]:
    return cast(
        list["gs.DeveloperMetadata"],
        spreadsheet.metadata._data,  # type: ignore[reportPrivateUsage]
    )


def _sheet_metadata_raw(sheet: "Sheet") -> list["gs.DeveloperMetadata"]:
    return cast(
        list["gs.DeveloperMetadata"],
        sheet._metadata._data,  # type: ignore[reportPrivateUsage]
    )


def _sheet_merges_raw(sheet: "Sheet") -> list["gs.GridRange"]:
    return cast(
        list["gs.GridRange"],
        sheet._merges,  # type: ignore[reportPrivateUsage]
    )


# ============================================================================
# Cell value encoding
# ============================================================================

def _encode_cell_value(
    value: "gs.ExtendedValue | None",
    nf_type: str | None,
) -> CellValueJSON:
    if value is None:
        return None
    f = value.get("formulaValue")
    if f is not None:
        return f if f else None
    e = value.get("errorValue")
    if e is not None:
        err_type = e.get("type", "ERROR")
        if err_type == "ERROR_TYPE_UNSPECIFIED":
            err_type = "ERROR"
        msg = e.get("message", "")
        if msg:
            return {"error": err_type, "message": msg}
        return {"error": err_type}
    b = value.get("boolValue")
    if b is not None:
        return b
    n = value.get("numberValue")
    if n is not None:
        if nf_type in _DATE_FORMAT_TYPES:
            return _serial_to_iso(n, nf_type)
        return n
    s = value.get("stringValue")
    if s is not None:
        return s if s else None
    return None


def _serial_to_iso(serial: float, nf_type: str) -> str:
    days = int(serial)
    fraction = serial - days
    dt = _DATE_EPOCH + timedelta(days=days, seconds=fraction * 86400)
    if nf_type == "DATE":
        return dt.date().isoformat()
    if nf_type == "TIME":
        return dt.time().isoformat()
    return dt.isoformat()


# ============================================================================
# Cell format extraction
# ============================================================================

def _extract_cell_format(
    fmt: "gs.CellFormat",
    default: "gs.CellFormat",
) -> CellFormatSnapshot:
    result: dict[str, Any] = {}

    nf = _number_format_string(fmt.get("numberFormat"))
    default_nf = _number_format_string(default.get("numberFormat"))
    if nf is not None and nf != default_nf:
        result["number_format"] = nf

    bg = color_object_to_string(fmt.get("backgroundColorStyle"))
    default_bg = color_object_to_string(default.get("backgroundColorStyle"))
    if bg and bg != default_bg:
        result["bg"] = bg

    tf = fmt.get("textFormat", {})
    default_tf = default.get("textFormat", {})

    fg = color_object_to_string(tf.get("foregroundColorStyle"))
    default_fg = color_object_to_string(default_tf.get("foregroundColorStyle"))
    if fg and fg != default_fg:
        result["fg"] = fg

    p = fmt.get("padding")
    default_p = default.get("padding")
    if p and p != default_p:
        result["padding"] = [
            p.get("top", 0),
            p.get("right", 0),
            p.get("bottom", 0),
            p.get("left", 0),
        ]

    halign = fmt.get("horizontalAlignment")
    default_halign = default.get("horizontalAlignment")
    if (
        halign
        and halign != "HORIZONTAL_ALIGN_UNSPECIFIED"
        and halign != default_halign
    ):
        result["halign"] = halign

    valign = fmt.get("verticalAlignment")
    default_valign = default.get("verticalAlignment")
    if (
        valign
        and valign != "VERTICAL_ALIGN_UNSPECIFIED"
        and valign != default_valign
    ):
        result["valign"] = valign

    wrap = fmt.get("wrapStrategy")
    default_wrap = default.get("wrapStrategy")
    if (
        wrap
        and wrap != "WRAP_STRATEGY_UNSPECIFIED"
        and wrap != default_wrap
    ):
        result["wrap"] = wrap

    ff = tf.get("fontFamily")
    default_ff = default_tf.get("fontFamily")
    if ff and ff != default_ff:
        result["font_family"] = ff

    fs = tf.get("fontSize")
    default_fs = default_tf.get("fontSize", 10)
    if fs is not None and fs != default_fs:
        result["font_size"] = fs

    for key in ("bold", "italic", "underline", "strikethrough"):
        val = tf.get(key, False)
        default_val = default_tf.get(key, False)
        if val and val != default_val:
            result[key] = True

    return cast(
        CellFormatSnapshot, _order_keys(result, _CELL_FORMAT_KEY_ORDER)
    )


def _number_format_string(nf: "gs.NumberFormat | None") -> str | None:
    if nf is None:
        return None
    t = nf.get("type")
    if t is None or t == "NUMBER_FORMAT_TYPE_UNSPECIFIED":
        return None
    pattern = nf.get("pattern", "")
    if pattern:
        return f"{t}({pattern})"
    return t


# ============================================================================
# Border extraction & line compaction
# ============================================================================

def _collect_borders(
    borders: "gs.Borders",
    row: int,
    col: int,
    edges: dict[tuple[str, str, int, str | None], set[tuple[int, int]]],
) -> bool:
    """Canonicalize the cell's four borders to bottom/right perspective."""
    found = False

    bottom = borders.get("bottom")
    if bottom and _border_meaningful(bottom):
        key = _border_key("horizontal", bottom)
        edges.setdefault(key, set()).add((row, col))
        found = True

    if row > 0:
        top = borders.get("top")
        if top and _border_meaningful(top):
            key = _border_key("horizontal", top)
            edges.setdefault(key, set()).add((row - 1, col))
            found = True

    right = borders.get("right")
    if right and _border_meaningful(right):
        key = _border_key("vertical", right)
        edges.setdefault(key, set()).add((row, col))
        found = True

    if col > 0:
        left = borders.get("left")
        if left and _border_meaningful(left):
            key = _border_key("vertical", left)
            edges.setdefault(key, set()).add((row, col - 1))
            found = True

    return found


def _border_meaningful(border: "gs.Border") -> bool:
    style = border.get("style")
    width = border.get("width", 0)
    if not style or style in ("NONE", "STYLE_UNSPECIFIED"):
        return False
    return width > 0


def _border_key(
    direction: str, border: "gs.Border"
) -> tuple[str, str, int, str | None]:
    style = border.get("style") or "SOLID"
    width = border.get("width", 0)
    color = color_object_to_string(border.get("colorStyle"))
    return (direction, style, width, color)


def _build_borders(
    edges: dict[tuple[str, str, int, str | None], set[tuple[int, int]]],
    data_rows: int,
    data_cols: int,
) -> BordersSnapshot:
    horizontal: list[list[Any]] = []
    vertical: list[list[Any]] = []
    for (direction, style, width, color), cells in edges.items():
        if not cells:
            continue
        range_str = _compact_cells_to_ranges(cells, data_rows, data_cols)
        spec: dict[str, Any] = {"style": style, "width": width}
        if color and color != "#000000":
            spec["color"] = color
        if direction == "horizontal":
            horizontal.append([range_str, spec])
        else:
            vertical.append([range_str, spec])
    horizontal.sort(key=lambda e: _range_sort_key(cast(str, e[0])))
    vertical.sort(key=lambda e: _range_sort_key(cast(str, e[0])))
    result: dict[str, Any] = {}
    if horizontal:
        result["horizontal"] = horizontal
    if vertical:
        result["vertical"] = vertical
    return cast(BordersSnapshot, result)


# ============================================================================
# Range compaction (greedy)
# ============================================================================

def _compact_to_range_list(
    cells: set[tuple[int, int]],
    data_rows: int,
    data_cols: int,
) -> list[str]:
    """Like _compact_cells_to_ranges but returns the parts as a sorted list."""
    if not cells:
        return []
    compacted = _compact_cells_to_ranges(cells, data_rows, data_cols)
    if not compacted:
        return []
    parts = compacted.split(",")
    parts.sort(key=_range_sort_key)
    return parts


def _compact_cells_to_ranges(
    cells: set[tuple[int, int]],
    data_rows: int,
    data_cols: int,
) -> str:
    if not cells:
        return ""
    remaining = set(cells)
    parts: list[str] = []

    by_col: dict[int, set[int]] = {}
    by_row: dict[int, set[int]] = {}
    for r, c in remaining:
        by_col.setdefault(c, set()).add(r)
        by_row.setdefault(r, set()).add(c)

    # Require at least 2 rows/cols for "full" detection — otherwise a single-cell
    # extent would degenerate into "A:A" instead of "A1".
    full_cols = {
        c
        for c, rows in by_col.items()
        if data_rows >= 2 and len(rows) == data_rows
    }
    full_rows = {
        r
        for r, cols in by_row.items()
        if data_cols >= 2 and len(cols) == data_cols
    }

    for c in sorted(full_cols):
        letter = _col_to_letter(c)
        parts.append(f"{letter}:{letter}")
        for r in range(data_rows):
            remaining.discard((r, c))

    for r in sorted(full_rows):
        if not any(
            (r, c) in remaining
            for c in range(data_cols)
            if c not in full_cols
        ):
            # Already covered by full columns above.
            continue
        parts.append(f"{r + 1}:{r + 1}")
        for c in range(data_cols):
            remaining.discard((r, c))

    while remaining:
        r0, c0 = min(remaining)
        c1 = c0
        while (r0, c1 + 1) in remaining:
            c1 += 1
        r1 = r0
        while all((r1 + 1, c) in remaining for c in range(c0, c1 + 1)):
            r1 += 1
        if r0 == r1 and c0 == c1:
            parts.append(coords_to_address(r0, c0))
        else:
            parts.append(
                f"{coords_to_address(r0, c0)}:{coords_to_address(r1, c1)}"
            )
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                remaining.discard((r, c))

    return ",".join(parts)


def _range_sort_key(range_str: str) -> tuple[int, int, str]:
    """Sort key that orders ranges by their starting (row, col) numerically."""
    first = range_str.split(",", 1)[0].split(":", 1)[0]
    if not first:
        return (0, 0, range_str)
    letters = ""
    digits = ""
    for ch in first:
        if ch.isalpha():
            letters += ch
        elif ch.isdigit():
            digits += ch
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    row = int(digits) if digits else 0
    return (row if row else 0, col, range_str)


def _col_to_letter(col: int) -> str:
    if col < 0:
        raise ValueError(f"Invalid column index: {col}")
    s = ""
    i = col + 1
    while i:
        s = chr(ord("A") + ((i - 1) % 26)) + s
        i = (i - 1) // 26
    return s


def _addr_dict(
    items: dict[tuple[int, int], Any],
) -> dict[str, Any]:
    """Sort (row, col) → value dict by (row, col) and return address-keyed dict."""
    result: dict[str, Any] = {}
    for (r, c) in sorted(items.keys()):
        result[coords_to_address(r, c)] = items[(r, c)]
    return result


# ============================================================================
# Key ordering
# ============================================================================

def _order_keys(d: dict[str, Any], order: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k in order:
        if k in d:
            result[k] = d[k]
    for k in sorted(d.keys()):
        if k not in result:
            result[k] = d[k]
    return result


# ============================================================================
# JSON emitter with inline-control
# ============================================================================

class _Inline:
    """Marker: serialize the wrapped value on a single line."""

    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value


def _wrap_for_emit(snap: SpreadsheetSnapshot) -> dict[str, Any]:
    """Wrap inline-worthy substructures with _Inline markers for the emitter."""
    return {
        "schema_version": snap["schema_version"],
        "spreadsheet": _wrap_spreadsheet_meta(snap["spreadsheet"]),
        "sheets": [_wrap_sheet(s) for s in snap["sheets"]],
    }


def _wrap_spreadsheet_meta(meta: SpreadsheetMetaSnapshot) -> dict[str, Any]:
    out: dict[str, Any] = dict(meta)
    if "metadata" in out:
        out["metadata"] = [_Inline(dict(m)) for m in out["metadata"]]
    return out


def _wrap_sheet(sheet: SheetSnapshot) -> dict[str, Any]:
    out: dict[str, Any] = dict(sheet)
    if "grid" in out:
        out["grid"] = _Inline(dict(out["grid"]))
    if "frozen" in out:
        out["frozen"] = _Inline(dict(out["frozen"]))
    if "data" in out:
        out["data"] = [_Inline(row) for row in out["data"]]
    if "formats" in out:
        out["formats"] = [_Inline(dict(entry)) for entry in out["formats"]]
    if "borders" in out:
        borders: dict[str, Any] = dict(out["borders"])
        if "horizontal" in borders:
            borders["horizontal"] = [_Inline(list(e)) for e in borders["horizontal"]]
        if "vertical" in borders:
            borders["vertical"] = [_Inline(list(e)) for e in borders["vertical"]]
        out["borders"] = borders
    if "rows" in out:
        out["rows"] = {k: _Inline(dict(v)) for k, v in out["rows"].items()}
    if "columns" in out:
        out["columns"] = {k: _Inline(dict(v)) for k, v in out["columns"].items()}
    if "metadata" in out:
        out["metadata"] = [_Inline(dict(m)) for m in out["metadata"]]
    return out


def _emit(value: Any, depth: int) -> str:
    pad = "  " * depth
    inner_pad = "  " * (depth + 1)

    if isinstance(value, _Inline):
        return json.dumps(
            value.value, ensure_ascii=False, separators=(", ", ": ")
        )

    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        if not d:
            return "{}"
        items: list[str] = []
        for k, v in d.items():
            key_str = json.dumps(k, ensure_ascii=False)
            items.append(f"{inner_pad}{key_str}: {_emit(v, depth + 1)}")
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"

    if isinstance(value, list):
        lst = cast(list[Any], value)
        if not lst:
            return "[]"
        items = [f"{inner_pad}{_emit(item, depth + 1)}" for item in lst]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"

    return json.dumps(value, ensure_ascii=False)
