"""
Unit tests for the pure helpers in `gservices.sheets.snapshot`.

These tests touch only stateless functions — no Spreadsheet, no Google API
mocks. They cover the algorithmic surface: A1 letter conversion, range
compaction, date serial decoding, key ordering, and the custom JSON emitter.
"""

import json
from typing import TYPE_CHECKING

from gservices.sheets.snapshot import (
    _Inline,
    _addr_dict,
    _col_to_letter,
    _compact_cells_to_ranges,
    _compact_to_range_list,
    _emit,
    _encode_cell_value,
    _extract_cell_format,
    _number_format_string,
    _order_keys,
    _range_sort_key,
    _serial_to_iso,
)

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


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
        empty: "gs.CellFormat" = {}
        assert _extract_cell_format(empty, empty) == {}

    def test_only_bold_differs(self):
        fmt: "gs.CellFormat" = {"textFormat": {"bold": True, "fontSize": 10}}
        default: "gs.CellFormat" = {"textFormat": {"fontSize": 10}}
        assert _extract_cell_format(fmt, default) == {"bold": True}

    def test_subtracts_default(self):
        # Cell has bold=True; default also has bold=True — should not appear.
        fmt: "gs.CellFormat" = {"textFormat": {"bold": True}}
        default: "gs.CellFormat" = {"textFormat": {"bold": True}}
        assert _extract_cell_format(fmt, default) == {}

    def test_background_color(self):
        fmt: "gs.CellFormat" = {"backgroundColorStyle": {"rgbColor": {"red": 1.0}}}
        default: "gs.CellFormat" = {}
        result = _extract_cell_format(fmt, default)
        bg = result.get("bg")
        assert bg is not None
        assert bg.startswith("#ff")

    def test_horizontal_alignment(self):
        fmt: "gs.CellFormat" = {"horizontalAlignment": "CENTER"}
        default: "gs.CellFormat" = {}
        assert _extract_cell_format(fmt, default) == {"halign": "CENTER"}

    def test_unspecified_alignment_omitted(self):
        fmt: "gs.CellFormat" = {"horizontalAlignment": "HORIZONTAL_ALIGN_UNSPECIFIED"}
        default: "gs.CellFormat" = {}
        assert _extract_cell_format(fmt, default) == {}

    def test_key_ordering(self):
        # Output keys should follow _CELL_FORMAT_KEY_ORDER, not insertion order.
        fmt: "gs.CellFormat" = {
            "textFormat": {"bold": True, "italic": True},
            "horizontalAlignment": "CENTER",
            "backgroundColorStyle": {"rgbColor": {"red": 1.0}},
        }
        default: "gs.CellFormat" = {}
        keys = list(_extract_cell_format(fmt, default).keys())
        # bg comes before halign comes before bold/italic
        assert keys.index("bg") < keys.index("halign")
        assert keys.index("halign") < keys.index("bold")
        assert keys.index("bold") < keys.index("italic")

    def test_padding_serialized_as_trbl_list(self):
        fmt: "gs.CellFormat" = {
            "padding": {"top": 1, "right": 2, "bottom": 3, "left": 4}
        }
        default: "gs.CellFormat" = {}
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
