"""
Unit tests for the pure helpers in `gservices.sheets.snapshot`.

These tests touch only stateless functions — no Spreadsheet, no Google API
mocks. They cover the algorithmic surface: A1 letter conversion, range
compaction, date serial decoding, key ordering, and the custom JSON emitter.
"""

import json
from typing import TYPE_CHECKING

from gservices.sheets.snapshot import (
    _addr_dict,
    _border_key,
    _border_meaningful,
    _build_borders,
    _build_col_meta,
    _build_merges,
    _build_metadata_items,
    _build_row_meta,
    _build_theme,
    _col_to_letter,
    _collect_borders,
    _compact_cells_to_ranges,
    _compact_to_range_list,
    _emit,
    _encode_cell_value,
    _extract_cell_format,
    _Inline,
    _number_format_string,
    _order_keys,
    _range_sort_key,
    _serial_to_iso,
)
from gservices.sheets.utils import _RGB_CACHE, color_object_to_string

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

# Shape of the per-direction border accumulator used by _collect_borders and
# _build_borders. The key is (direction, style, width, color); the value is
# the set of cells whose edge carries that border style.
EdgesDict = dict[tuple[str, str, int, str | None], set[tuple[int, int]]]


# ----------------------------------------------------------------------------
# _col_to_letter
# ----------------------------------------------------------------------------

class TestColToLetter:
    def test_single_letter_columns(self):
        assert _col_to_letter(0) == "A"
        assert _col_to_letter(1) == "B"
        assert _col_to_letter(25) == "Z"

    def test_double_letter_columns(self):
        assert _col_to_letter(26) == "AA"
        assert _col_to_letter(27) == "AB"
        assert _col_to_letter(51) == "AZ"
        assert _col_to_letter(52) == "BA"
        assert _col_to_letter(701) == "ZZ"

    def test_triple_letter_columns(self):
        assert _col_to_letter(702) == "AAA"
        assert _col_to_letter(703) == "AAB"


# ----------------------------------------------------------------------------
# _compact_cells_to_ranges
# ----------------------------------------------------------------------------

class TestCompactCellsToRanges:
    def test_empty(self):
        assert _compact_cells_to_ranges(set(), 0, 0) == ""

    def test_single_cell(self):
        assert _compact_cells_to_ranges({(0, 0)}, 1, 1) == "A1"
        assert _compact_cells_to_ranges({(2, 3)}, 10, 10) == "D3"

    def test_horizontal_run_becomes_rectangle(self):
        # Three adjacent cells in row 0
        assert _compact_cells_to_ranges({(0, 0), (0, 1), (0, 2)}, 10, 10) == "A1:C1"

    def test_vertical_run_becomes_rectangle(self):
        assert _compact_cells_to_ranges({(0, 0), (1, 0), (2, 0)}, 10, 10) == "A1:A3"

    def test_rectangle(self):
        cells = {(r, c) for r in range(3) for c in range(2)}
        assert _compact_cells_to_ranges(cells, 10, 10) == "A1:B3"

    def test_full_column(self):
        # 3-row data extent, all cells in column B
        assert _compact_cells_to_ranges({(0, 1), (1, 1), (2, 1)}, 3, 3) == "B:B"

    def test_full_row(self):
        assert _compact_cells_to_ranges({(1, 0), (1, 1), (1, 2)}, 3, 3) == "2:2"

    def test_single_cell_extent_does_not_become_full_column(self):
        # Without the >=2 guard, a 1x1 extent would degenerate to "A:A".
        assert _compact_cells_to_ranges({(0, 0)}, 1, 1) == "A1"

    def test_full_column_plus_isolated(self):
        # Column A fully covered (4 cells), plus one outlier
        cells = {(0, 0), (1, 0), (2, 0), (3, 0), (3, 5)}
        assert _compact_cells_to_ranges(cells, 4, 10) == "A:A,F4"

    def test_ring_with_hole(self):
        # 3x3 block missing the center
        cells = {(r, c) for r in range(3) for c in range(3)} - {(1, 1)}
        result = _compact_cells_to_ranges(cells, 10, 10)
        # Greedy: top row first (A1:C1), then left column under (A2:A3),
        # then right column (C2:C3), then the lone B3.
        assert result == "A1:C1,A2:A3,C2:C3,B3"

    def test_separated_rectangles(self):
        cells = (
            {(r, c) for r in range(3) for c in range(2)}  # A1:B3
            | {(4, 3)}  # D5
        )
        assert _compact_cells_to_ranges(cells, 10, 10) == "A1:B3,D5"


# ----------------------------------------------------------------------------
# _compact_to_range_list
# ----------------------------------------------------------------------------

class TestCompactToRangeList:
    def test_empty(self):
        assert _compact_to_range_list(set(), 0, 0) == []

    def test_sorted_output(self):
        cells = {(0, 0), (1, 0), (2, 0), (3, 0), (3, 5)}
        # data_rows=4, so column A is "full"
        result = _compact_to_range_list(cells, 4, 10)
        assert result == ["A:A", "F4"]


# ----------------------------------------------------------------------------
# _range_sort_key
# ----------------------------------------------------------------------------

class TestRangeSortKey:
    def test_numerical_row_ordering(self):
        # "A2" should sort before "A10" — string sort would put "A10" first.
        keys = sorted(["A10", "A2", "A1"], key=_range_sort_key)
        assert keys == ["A1", "A2", "A10"]

    def test_columns_within_row(self):
        keys = sorted(["C1", "A1", "B1"], key=_range_sort_key)
        assert keys == ["A1", "B1", "C1"]

    def test_double_letter_columns(self):
        keys = sorted(["AA1", "Z1", "A1"], key=_range_sort_key)
        assert keys == ["A1", "Z1", "AA1"]


# ----------------------------------------------------------------------------
# _serial_to_iso
# ----------------------------------------------------------------------------

class TestSerialToIso:
    def test_date_epoch(self):
        # Day 0 = 1899-12-30 (Google Sheets / Excel epoch)
        assert _serial_to_iso(0.0, "DATE") == "1899-12-30"

    def test_known_date(self):
        # 2024-06-15 — day 45458 from the epoch
        assert _serial_to_iso(45458.0, "DATE") == "2024-06-15"

    def test_time_noon(self):
        assert _serial_to_iso(0.5, "TIME") == "12:00:00"

    def test_time_quarter_day(self):
        assert _serial_to_iso(0.25, "TIME") == "06:00:00"

    def test_date_time(self):
        # 2024-06-15 at noon
        assert _serial_to_iso(45458.5, "DATE_TIME") == "2024-06-15T12:00:00"


# ----------------------------------------------------------------------------
# _encode_cell_value
# ----------------------------------------------------------------------------

class TestEncodeCellValue:
    def test_none(self):
        assert _encode_cell_value(None, None) is None

    def test_string(self):
        assert _encode_cell_value({"stringValue": "hello"}, None) == "hello"

    def test_empty_string_becomes_none(self):
        assert _encode_cell_value({"stringValue": ""}, None) is None

    def test_number(self):
        assert _encode_cell_value({"numberValue": 42.5}, None) == 42.5

    def test_number_with_date_format(self):
        assert _encode_cell_value({"numberValue": 45458.0}, "DATE") == "2024-06-15"

    def test_bool_true(self):
        assert _encode_cell_value({"boolValue": True}, None) is True

    def test_bool_false(self):
        assert _encode_cell_value({"boolValue": False}, None) is False

    def test_formula_bare_string(self):
        # Formulas come back as bare strings, not tagged dicts.
        assert _encode_cell_value({"formulaValue": "=A1+B1"}, None) == "=A1+B1"

    def test_error(self):
        result = _encode_cell_value(
            {"errorValue": {"type": "REF", "message": "Bad ref"}}, None
        )
        assert result == {"error": "REF", "message": "Bad ref"}

    def test_error_without_message(self):
        result = _encode_cell_value({"errorValue": {"type": "NAME"}}, None)
        assert result == {"error": "NAME"}

    def test_error_unspecified_type(self):
        result = _encode_cell_value(
            {"errorValue": {"type": "ERROR_TYPE_UNSPECIFIED"}}, None
        )
        assert result == {"error": "ERROR"}


# ----------------------------------------------------------------------------
# _number_format_string
# ----------------------------------------------------------------------------

class TestColorObjectToString:
    """Direct tests for the cached `color_object_to_string`."""

    def test_none(self):
        assert color_object_to_string(None) is None

    def test_rgb_red(self):
        assert color_object_to_string({"rgbColor": {"red": 1.0}}) == "#ff0000"

    def test_rgb_with_alpha(self):
        result = color_object_to_string(
            {"rgbColor": {"red": 1.0, "alpha": 0.5}}
        )
        # alpha != 1 → 4th byte present (0.5 * 255 + 0.1 → 127 → 7f)
        assert result == "#ff00007f"

    def test_rgb_full_opaque_strips_alpha(self):
        # alpha == 1 → "ff" suffix is stripped for a 6-char hex.
        assert color_object_to_string(
            {"rgbColor": {"red": 0, "green": 0, "blue": 0, "alpha": 1}}
        ) == "#000000"

    def test_theme_color(self):
        assert color_object_to_string({"themeColor": "ACCENT1"}) == "ACCENT1"

    def test_theme_unspecified(self):
        assert color_object_to_string(
            {"themeColor": "THEME_COLOR_TYPE_UNSPECIFIED"}
        ) is None

    def test_distinct_dicts_same_content_share_cache_entry(self):
        # The cache deduplicates by VALUE, not identity. Two equal-but-distinct
        # dicts should produce the same string AND only add one cache entry.
        _RGB_CACHE.clear()
        d1: gs.ColorStyle = {"rgbColor": {"red": 0.5, "green": 0.25, "blue": 0.75}}
        d2: gs.ColorStyle = {"rgbColor": {"red": 0.5, "green": 0.25, "blue": 0.75}}
        assert d1 is not d2
        s1 = color_object_to_string(d1)
        s2 = color_object_to_string(d2)
        assert s1 == s2
        assert len(_RGB_CACHE) == 1


class TestNumberFormatString:
    def test_none(self):
        assert _number_format_string(None) is None

    def test_unspecified(self):
        assert _number_format_string({"type": "NUMBER_FORMAT_TYPE_UNSPECIFIED"}) is None

    def test_type_only(self):
        assert _number_format_string({"type": "DATE"}) == "DATE"

    def test_type_with_pattern(self):
        result = _number_format_string({"type": "DATE", "pattern": "yyyy-mm-dd"})
        assert result == "DATE(yyyy-mm-dd)"


# ----------------------------------------------------------------------------
# _extract_cell_format
# ----------------------------------------------------------------------------

class TestExtractCellFormat:
    def test_empty(self):
        empty: gs.CellFormat = {}
        assert _extract_cell_format(empty, empty) == {}

    def test_only_bold_differs(self):
        fmt: gs.CellFormat = {"textFormat": {"bold": True, "fontSize": 10}}
        default: gs.CellFormat = {"textFormat": {"fontSize": 10}}
        assert _extract_cell_format(fmt, default) == {"bold": True}

    def test_subtracts_default(self):
        # Cell has bold=True; default also has bold=True — should not appear.
        fmt: gs.CellFormat = {"textFormat": {"bold": True}}
        default: gs.CellFormat = {"textFormat": {"bold": True}}
        assert _extract_cell_format(fmt, default) == {}

    def test_background_color(self):
        fmt: gs.CellFormat = {"backgroundColorStyle": {"rgbColor": {"red": 1.0}}}
        default: gs.CellFormat = {}
        result = _extract_cell_format(fmt, default)
        bg = result.get("bg")
        assert bg is not None
        assert bg.startswith("#ff")

    def test_horizontal_alignment(self):
        fmt: gs.CellFormat = {"horizontalAlignment": "CENTER"}
        default: gs.CellFormat = {}
        assert _extract_cell_format(fmt, default) == {"halign": "CENTER"}

    def test_unspecified_alignment_omitted(self):
        fmt: gs.CellFormat = {"horizontalAlignment": "HORIZONTAL_ALIGN_UNSPECIFIED"}
        default: gs.CellFormat = {}
        assert _extract_cell_format(fmt, default) == {}

    def test_key_ordering(self):
        # Output keys should follow _CELL_FORMAT_KEY_ORDER, not insertion order.
        fmt: gs.CellFormat = {
            "textFormat": {"bold": True, "italic": True},
            "horizontalAlignment": "CENTER",
            "backgroundColorStyle": {"rgbColor": {"red": 1.0}},
        }
        default: gs.CellFormat = {}
        keys = list(_extract_cell_format(fmt, default).keys())
        # bg comes before halign comes before bold/italic
        assert keys.index("bg") < keys.index("halign")
        assert keys.index("halign") < keys.index("bold")
        assert keys.index("bold") < keys.index("italic")

    def test_padding_serialized_as_trbl_list(self):
        fmt: gs.CellFormat = {
            "padding": {"top": 1, "right": 2, "bottom": 3, "left": 4}
        }
        default: gs.CellFormat = {}
        result = _extract_cell_format(fmt, default)
        assert result.get("padding") == [1, 2, 3, 4]


# ----------------------------------------------------------------------------
# _order_keys
# ----------------------------------------------------------------------------

class TestOrderKeys:
    def test_keys_in_order(self):
        d = {"c": 3, "a": 1, "b": 2}
        ordered = _order_keys(d, ("a", "b", "c"))
        assert list(ordered.keys()) == ["a", "b", "c"]

    def test_missing_keys_skipped(self):
        d = {"a": 1, "c": 3}
        ordered = _order_keys(d, ("a", "b", "c"))
        assert list(ordered.keys()) == ["a", "c"]

    def test_unlisted_keys_sorted_at_end(self):
        d = {"z": 26, "a": 1, "m": 13}
        ordered = _order_keys(d, ("a",))
        assert list(ordered.keys()) == ["a", "m", "z"]


# ----------------------------------------------------------------------------
# _addr_dict
# ----------------------------------------------------------------------------

class TestAddrDict:
    def test_empty(self):
        assert _addr_dict({}) == {}

    def test_sorted_by_row_then_col(self):
        items = {(1, 2): "x", (0, 0): "y", (1, 0): "z"}
        result = _addr_dict(items)
        assert list(result.keys()) == ["A1", "A2", "C2"]
        assert result["A1"] == "y"

    def test_numeric_row_ordering(self):
        # (1, 0) → "A2", (10, 0) → "A11" — must sort numerically, not lexically.
        items = {(10, 0): "ten", (1, 0): "one", (2, 0): "two"}
        result = _addr_dict(items)
        assert list(result.keys()) == ["A2", "A3", "A11"]


# ----------------------------------------------------------------------------
# _emit (custom JSON emitter)
# ----------------------------------------------------------------------------

class TestEmit:
    def test_primitives(self):
        assert _emit("hello", 0) == '"hello"'
        assert _emit(42, 0) == "42"
        assert _emit(True, 0) == "true"
        assert _emit(None, 0) == "null"

    def test_empty_collections(self):
        assert _emit({}, 0) == "{}"
        assert _emit([], 0) == "[]"

    def test_flat_dict(self):
        out = _emit({"a": 1, "b": 2}, 0)
        assert out == '{\n  "a": 1,\n  "b": 2\n}'

    def test_nested_dict(self):
        out = _emit({"outer": {"inner": 1}}, 0)
        assert out == '{\n  "outer": {\n    "inner": 1\n  }\n}'

    def test_inline_dict_collapses(self):
        out = _emit({"row": _Inline({"a": 1, "b": 2})}, 0)
        assert out == '{\n  "row": {"a": 1, "b": 2}\n}'

    def test_inline_list_collapses(self):
        out = _emit({"row": _Inline([1, 2, 3])}, 0)
        assert out == '{\n  "row": [1, 2, 3]\n}'

    def test_list_of_inline_rows(self):
        # The canonical 2D-data layout: outer list multi-line, each row inline.
        out = _emit([_Inline([1, 2]), _Inline([3, 4])], 0)
        assert out == "[\n  [1, 2],\n  [3, 4]\n]"

    def test_output_is_valid_json(self):
        # Whatever the layout, the bytes must round-trip through json.loads.
        snap = {
            "schema_version": 1,
            "data": [_Inline([1, 2, 3]), _Inline([4, 5, 6])],
            "formats": [_Inline({"range": "A1", "fmt": {"bold": True}})],
            "nested": {"deep": {"key": "value"}},
        }
        text = _emit(snap, 0)
        parsed = json.loads(text)
        assert parsed["schema_version"] == 1
        assert parsed["data"] == [[1, 2, 3], [4, 5, 6]]
        assert parsed["formats"][0] == {"range": "A1", "fmt": {"bold": True}}
        assert parsed["nested"]["deep"]["key"] == "value"

    def test_unicode_passthrough(self):
        # ensure_ascii=False keeps characters as-is — important for readability.
        out = _emit("héllo", 0)
        assert out == '"héllo"'


# ----------------------------------------------------------------------------
# _border_meaningful
# ----------------------------------------------------------------------------

class TestBorderMeaningful:
    def test_empty(self):
        border: gs.Border = {}
        assert _border_meaningful(border) is False

    def test_style_none(self):
        border: gs.Border = {"style": "NONE", "width": 1}
        assert _border_meaningful(border) is False

    def test_style_unspecified(self):
        border: gs.Border = {"style": "STYLE_UNSPECIFIED", "width": 1}
        assert _border_meaningful(border) is False

    def test_zero_width(self):
        border: gs.Border = {"style": "SOLID", "width": 0}
        assert _border_meaningful(border) is False

    def test_solid_thin(self):
        border: gs.Border = {"style": "SOLID", "width": 1}
        assert _border_meaningful(border) is True

    def test_dotted_thick(self):
        border: gs.Border = {"style": "DOTTED", "width": 3}
        assert _border_meaningful(border) is True


# ----------------------------------------------------------------------------
# _border_key
# ----------------------------------------------------------------------------

class TestBorderKey:
    def test_groups_identical_borders(self):
        # Two borders that differ only by source-side direction should produce
        # the same key when given the same direction argument — that's how
        # multiple cells coalesce into a single line in the output.
        border1: gs.Border = {"style": "SOLID", "width": 1}
        border2: gs.Border = {"style": "SOLID", "width": 1}
        assert _border_key("horizontal", border1) == _border_key(
            "horizontal", border2
        )

    def test_distinguishes_by_direction(self):
        border: gs.Border = {"style": "SOLID", "width": 1}
        assert _border_key("horizontal", border) != _border_key(
            "vertical", border
        )

    def test_distinguishes_by_style(self):
        b1: gs.Border = {"style": "SOLID", "width": 1}
        b2: gs.Border = {"style": "DOTTED", "width": 1}
        assert _border_key("horizontal", b1) != _border_key("horizontal", b2)

    def test_distinguishes_by_color(self):
        b1: gs.Border = {
            "style": "SOLID",
            "width": 1,
            "colorStyle": {"rgbColor": {"red": 1.0}},
        }
        b2: gs.Border = {
            "style": "SOLID",
            "width": 1,
            "colorStyle": {"rgbColor": {"blue": 1.0}},
        }
        assert _border_key("horizontal", b1) != _border_key("horizontal", b2)


# ----------------------------------------------------------------------------
# _collect_borders
# ----------------------------------------------------------------------------

class TestCollectBorders:
    def test_no_borders(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {}
        assert _collect_borders(borders, 5, 5, edges) is False
        assert edges == {}

    def test_bottom_border_stays_as_bottom(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "bottom": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 2, 3, edges)
        # Exactly one horizontal entry covering cell (2, 3).
        assert len(edges) == 1
        key, cells = next(iter(edges.items()))
        assert key[0] == "horizontal"
        assert cells == {(2, 3)}

    def test_top_border_canonicalizes_to_bottom_of_prev_row(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "top": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 3, 5, edges)
        # Top of (3, 5) == bottom of (2, 5).
        cells = next(iter(edges.values()))
        assert cells == {(2, 5)}

    def test_top_of_row_zero_dropped(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "top": {"style": "SOLID", "width": 1},
        }
        # Row 0's top edge is the grid's outer boundary — dropped.
        assert _collect_borders(borders, 0, 5, edges) is False
        assert edges == {}

    def test_right_border_stays_as_right(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "right": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 2, 3, edges)
        assert len(edges) == 1
        key, cells = next(iter(edges.items()))
        assert key[0] == "vertical"
        assert cells == {(2, 3)}

    def test_left_border_canonicalizes_to_right_of_prev_col(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "left": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 4, 6, edges)
        cells = next(iter(edges.values()))
        assert cells == {(4, 5)}

    def test_left_of_col_zero_dropped(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "left": {"style": "SOLID", "width": 1},
        }
        assert _collect_borders(borders, 4, 0, edges) is False
        assert edges == {}

    def test_top_and_bottom_with_same_style_coalesce(self):
        # If cell (2, 0) has both top and bottom borders with identical style,
        # they should produce TWO entries in edges (bottom-of-1 and bottom-of-2),
        # both under the same horizontal key.
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "top": {"style": "SOLID", "width": 1},
            "bottom": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 2, 0, edges)
        assert len(edges) == 1
        cells = next(iter(edges.values()))
        assert cells == {(1, 0), (2, 0)}

    def test_all_four_borders(self):
        edges: EdgesDict = {}
        borders: gs.Borders = {
            "top": {"style": "SOLID", "width": 1},
            "bottom": {"style": "SOLID", "width": 1},
            "left": {"style": "SOLID", "width": 1},
            "right": {"style": "SOLID", "width": 1},
        }
        _collect_borders(borders, 2, 3, edges)
        # One horizontal key, one vertical key.
        directions = {k[0] for k in edges}
        assert directions == {"horizontal", "vertical"}
        horizontal_cells = next(
            cells for k, cells in edges.items() if k[0] == "horizontal"
        )
        vertical_cells = next(
            cells for k, cells in edges.items() if k[0] == "vertical"
        )
        # Top → (1, 3); bottom → (2, 3)
        assert horizontal_cells == {(1, 3), (2, 3)}
        # Left → (2, 2); right → (2, 3)
        assert vertical_cells == {(2, 2), (2, 3)}


# ----------------------------------------------------------------------------
# _build_borders
# ----------------------------------------------------------------------------

class TestBuildBorders:
    def test_empty(self):
        assert _build_borders({}, 0, 0) == {}

    def test_horizontal_line(self):
        edges: EdgesDict = {
            ("horizontal", "SOLID", 1, "#000000"): {(2, 0), (2, 1), (2, 2)},
        }
        result = _build_borders(edges, 10, 10)
        # Three consecutive cells in row 2 collapse to "A3:C3".
        assert result.get("horizontal") == [
            ["A3:C3", {"style": "SOLID", "width": 1}]
        ]
        assert "vertical" not in result

    def test_vertical_line(self):
        edges: EdgesDict = {
            ("vertical", "SOLID_THICK", 3, None): {(0, 5), (1, 5), (2, 5)},
        }
        result = _build_borders(edges, 10, 10)
        assert result.get("vertical") == [
            ["F1:F3", {"style": "SOLID_THICK", "width": 3}]
        ]

    def test_black_color_suppressed(self):
        # The default color #000000 is suppressed for compactness.
        edges: EdgesDict = {
            ("horizontal", "SOLID", 1, "#000000"): {(0, 0)},
        }
        result = _build_borders(edges, 10, 10)
        assert result.get("horizontal") == [["A1", {"style": "SOLID", "width": 1}]]

    def test_non_black_color_included(self):
        edges: EdgesDict = {
            ("horizontal", "SOLID", 1, "#FF0000"): {(0, 0)},
        }
        result = _build_borders(edges, 10, 10)
        assert result.get("horizontal") == [
            ["A1", {"style": "SOLID", "width": 1, "color": "#FF0000"}]
        ]

    def test_horizontal_and_vertical_together(self):
        edges: EdgesDict = {
            ("horizontal", "SOLID", 1, None): {(0, 0)},
            ("vertical", "SOLID", 1, None): {(0, 0)},
        }
        result = _build_borders(edges, 10, 10)
        assert "horizontal" in result
        assert "vertical" in result


# ----------------------------------------------------------------------------
# _build_theme
# ----------------------------------------------------------------------------

class TestBuildTheme:
    def test_empty(self):
        theme: gs.SpreadsheetTheme = {}
        assert _build_theme(theme) == {}

    def test_font_family_only(self):
        theme: gs.SpreadsheetTheme = {"primaryFontFamily": "Arial"}
        assert _build_theme(theme) == {"font_family": "Arial"}

    def test_colors_in_canonical_order(self):
        # Input is shuffled; output should follow the TEXT/BACKGROUND/LINK/ACCENT1..6 order.
        theme: gs.SpreadsheetTheme = {
            "themeColors": [
                {"colorType": "ACCENT3", "color": {"rgbColor": {"red": 0.3}}},
                {"colorType": "TEXT", "color": {"rgbColor": {}}},
                {"colorType": "ACCENT1", "color": {"rgbColor": {"red": 1.0}}},
                {"colorType": "BACKGROUND", "color": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}},
            ]
        }
        result = _build_theme(theme)
        assert "colors" in result
        keys = list(result["colors"].keys())
        assert keys == ["TEXT", "BACKGROUND", "ACCENT1", "ACCENT3"]

    def test_unspecified_color_type_skipped(self):
        theme: gs.SpreadsheetTheme = {
            "themeColors": [
                {"colorType": "THEME_COLOR_TYPE_UNSPECIFIED", "color": {"rgbColor": {}}},
                {"colorType": "TEXT", "color": {"rgbColor": {}}},
            ]
        }
        result = _build_theme(theme)
        assert "colors" in result
        assert list(result["colors"].keys()) == ["TEXT"]


# ----------------------------------------------------------------------------
# _build_merges
# ----------------------------------------------------------------------------

class TestBuildMerges:
    def test_empty(self):
        assert _build_merges([]) == []

    def test_multi_cell_merge(self):
        merges: list[gs.GridRange] = [
            {
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": 3,
            }
        ]
        # Three columns merged across one row.
        assert _build_merges(merges) == ["A1:C1"]

    def test_rectangle_merge(self):
        merges: list[gs.GridRange] = [
            {
                "startRowIndex": 2,
                "endRowIndex": 5,
                "startColumnIndex": 1,
                "endColumnIndex": 3,
            }
        ]
        assert _build_merges(merges) == ["B3:C5"]

    def test_degenerate_zero_extent_merge_skipped(self):
        merges: list[gs.GridRange] = [
            {
                "startRowIndex": 0,
                "endRowIndex": 0,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            }
        ]
        assert _build_merges(merges) == []

    def test_sorted_output(self):
        merges: list[gs.GridRange] = [
            {"startRowIndex": 5, "endRowIndex": 6, "startColumnIndex": 0, "endColumnIndex": 2},
            {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 2},
        ]
        # Earlier rows must come first.
        result = _build_merges(merges)
        assert result == ["A1:B1", "A6:B6"]


# ----------------------------------------------------------------------------
# _build_row_meta
# ----------------------------------------------------------------------------

class TestBuildRowMeta:
    def test_empty(self):
        assert _build_row_meta([]) == {}

    def test_default_height_omitted(self):
        # Default row height is 21 — should produce no entry.
        rows: list[gs.DimensionProperties] = [{"pixelSize": 21}, {"pixelSize": 21}]
        assert _build_row_meta(rows) == {}

    def test_non_default_height(self):
        rows: list[gs.DimensionProperties] = [{"pixelSize": 32}, {"pixelSize": 21}]
        assert _build_row_meta(rows) == {"0": {"height": 32}}

    def test_hidden(self):
        rows: list[gs.DimensionProperties] = [{"hiddenByUser": True}]
        assert _build_row_meta(rows) == {"0": {"hidden": True}}

    def test_height_and_hidden(self):
        rows: list[gs.DimensionProperties] = [
            {"pixelSize": 50, "hiddenByUser": True}
        ]
        assert _build_row_meta(rows) == {"0": {"height": 50, "hidden": True}}


# ----------------------------------------------------------------------------
# _build_col_meta
# ----------------------------------------------------------------------------

class TestBuildColMeta:
    def test_empty(self):
        assert _build_col_meta([]) == {}

    def test_default_width_omitted(self):
        cols: list[gs.DimensionProperties] = [{"pixelSize": 100}, {"pixelSize": 100}]
        assert _build_col_meta(cols) == {}

    def test_non_default_width(self):
        cols: list[gs.DimensionProperties] = [
            {"pixelSize": 180},
            {"pixelSize": 100},
            {"pixelSize": 120},
        ]
        # Column letters as keys.
        assert _build_col_meta(cols) == {"A": {"width": 180}, "C": {"width": 120}}

    def test_double_letter_column(self):
        # Position 26 → "AA"
        default: gs.DimensionProperties = {"pixelSize": 100}
        wide: gs.DimensionProperties = {"pixelSize": 200}
        cols: list[gs.DimensionProperties] = [default] * 26 + [wide]
        result = _build_col_meta(cols)
        assert "AA" in result
        assert result["AA"] == {"width": 200}


# ----------------------------------------------------------------------------
# _build_metadata_items
# ----------------------------------------------------------------------------

class TestBuildMetadataItems:
    def test_empty(self):
        assert _build_metadata_items([]) == []

    def test_document_visibility_is_public(self):
        data: list[gs.DeveloperMetadata] = [
            {"metadataKey": "owner", "metadataValue": "alice", "visibility": "DOCUMENT"}
        ]
        assert _build_metadata_items(data) == [
            {"key": "owner", "value": "alice", "public": True}
        ]

    def test_project_visibility_is_private(self):
        data: list[gs.DeveloperMetadata] = [
            {"metadataKey": "k", "metadataValue": "v", "visibility": "PROJECT"}
        ]
        assert _build_metadata_items(data) == [
            {"key": "k", "value": "v", "public": False}
        ]

    def test_sorted_by_key_then_value(self):
        data: list[gs.DeveloperMetadata] = [
            {"metadataKey": "b", "metadataValue": "2", "visibility": "PROJECT"},
            {"metadataKey": "a", "metadataValue": "z", "visibility": "PROJECT"},
            {"metadataKey": "a", "metadataValue": "a", "visibility": "PROJECT"},
        ]
        result = _build_metadata_items(data)
        keys = [(m["key"], m["value"]) for m in result]
        # Both "a" entries come first (sorted by value), then "b".
        assert keys == [("a", "a"), ("a", "z"), ("b", "2")]

    def test_id_field_not_included(self):
        # Server-assigned metadataId should not appear in the snapshot,
        # so re-imports don't produce ID-shuffle diffs.
        data: list[gs.DeveloperMetadata] = [
            {
                "metadataId": 12345,
                "metadataKey": "k",
                "metadataValue": "v",
                "visibility": "DOCUMENT",
            }
        ]
        result = _build_metadata_items(data)
        assert "id" not in result[0]
        assert "metadataId" not in result[0]
