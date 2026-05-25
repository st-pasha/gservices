"""
Unit tests for core `gservices.sheets` behaviors — `Sheet`, `Row`, `Column`,
`Cell`, and `DeveloperMetadata`. These exercise the parts of the wrapper that
don't depend on the snapshot pipeline.
"""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from gservices.sheets.cell_value import Formula, HyperlinkFormula
from gservices.sheets.spreadsheet import Spreadsheet

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


# ----------------------------------------------------------------------------
# Fixture helpers
# ----------------------------------------------------------------------------

def _make_spreadsheet(data: dict[str, Any]) -> Spreadsheet:
    return Spreadsheet(cast("gs.Spreadsheet", data), MagicMock())


def _sheet_data(
    *,
    row_data: list[dict[str, Any]] | None = None,
    row_meta: list[dict[str, Any]] | None = None,
    col_meta: list[dict[str, Any]] | None = None,
    sheet_id: int = 0,
    title: str = "Sheet1",
    row_count: int = 1000,
    col_count: int = 26,
) -> dict[str, Any]:
    """Build a single-sheet API response. `row_data is None` produces a sheet
    with `"data": []` — the not-yet-loaded shape."""
    sheet: dict[str, Any] = {
        "properties": {
            "sheetId": sheet_id,
            "title": title,
            "index": 0,
            "gridProperties": {"rowCount": row_count, "columnCount": col_count},
        },
    }
    if row_data is None:
        sheet["data"] = []
    else:
        block: dict[str, Any] = {"rowData": row_data}
        if row_meta is not None:
            block["rowMetadata"] = row_meta
        if col_meta is not None:
            block["columnMetadata"] = col_meta
        sheet["data"] = [block]
    return {
        "spreadsheetId": "TEST",
        "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
        "sheets": [sheet],
    }


def _row(*values: Any) -> dict[str, Any]:
    """Build a `RowData` dict containing the given user-entered string values."""
    def cell(v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if isinstance(v, bool):
            return {"userEnteredValue": {"boolValue": v}}
        if isinstance(v, (int, float)):
            return {"userEnteredValue": {"numberValue": v}}
        return {"userEnteredValue": {"stringValue": str(v)}}
    return {"values": [cell(v) for v in values]}


# ----------------------------------------------------------------------------
# Columns
# ----------------------------------------------------------------------------

class TestColumnsLen:
    """`Columns.__len__` should reflect actual cell columns, not dict-key
    count or anything else."""

    def test_counts_cells_in_first_row(self):
        data = _sheet_data(row_data=[_row("a", "b", "c", "d", "e")])
        ss = _make_spreadsheet(data)
        assert len(ss.sheets[0].columns) == 5

    def test_empty_row_data_is_zero_columns(self):
        # When grid data is loaded but contains no rows, columns.__len__
        # should return 0 (not crash with IndexError).
        data = _sheet_data(row_data=[])
        ss = _make_spreadsheet(data)
        assert len(ss.sheets[0].columns) == 0

    def test_does_not_confuse_rowdata_dict_keys_for_columns(self):
        # A RowData dict has only one or two keys ("values", maybe others).
        # Make sure we measure values, not keys, by giving more columns than
        # the RowData dict could plausibly have keys.
        wide_row = _row(*range(10))
        data = _sheet_data(row_data=[wide_row])
        ss = _make_spreadsheet(data)
        assert len(ss.sheets[0].columns) == 10


# ----------------------------------------------------------------------------
# Row.next_row / previous_row
# ----------------------------------------------------------------------------

class TestRowNavigation:
    """`Row.next_row` / `previous_row` must compare against the row count, not
    the column count (a sheet wider than tall used to break this)."""

    def test_next_row_at_end_returns_none(self):
        data = _sheet_data(row_data=[_row("a"), _row("b"), _row("c")])
        rows = _make_spreadsheet(data).sheets[0].rows
        assert rows[2].next_row is None
        assert rows[1].next_row is rows[2]
        assert rows[0].next_row is rows[1]

    def test_next_row_on_wide_short_sheet(self):
        # 2 rows, 10 columns. Old code used len(self) == column_count == 10,
        # so rows[1].next_row would (wrongly) try to return rows[2].
        data = _sheet_data(row_data=[_row(*range(10)), _row(*range(10))])
        rows = _make_spreadsheet(data).sheets[0].rows
        assert rows[1].next_row is None
        assert rows[0].next_row is rows[1]

    def test_previous_row(self):
        data = _sheet_data(row_data=[_row("a"), _row("b"), _row("c")])
        rows = _make_spreadsheet(data).sheets[0].rows
        assert rows[0].previous_row is None
        assert rows[1].previous_row is rows[0]
        assert rows[2].previous_row is rows[1]


# ----------------------------------------------------------------------------
# Sheet init
# ----------------------------------------------------------------------------

class TestSheetInit:
    """`Sheet.__init__` must not crash on shapes the API actually emits — in
    particular `"data": []` for sheets where the grid wasn't requested."""

    def test_empty_data_list_does_not_crash(self):
        # API returns "data": [] for sheets fetched without includeGridData.
        # Old code's `data.get("data", [None])[0]` would IndexError.
        data = _sheet_data(row_data=None)
        ss = _make_spreadsheet(data)
        sheet = ss.sheets[0]
        assert sheet._cell_data is None

    def test_missing_data_key(self):
        # And the other no-grid shape: "data" key absent entirely.
        data = _sheet_data(row_data=None)
        del data["sheets"][0]["data"]
        ss = _make_spreadsheet(data)
        assert ss.sheets[0]._cell_data is None

    def test_data_present(self):
        # When data is present, _cell_data is the first block.
        data = _sheet_data(row_data=[_row("a", "b")])
        sheet = _make_spreadsheet(data).sheets[0]
        assert sheet._cell_data is not None
        assert sheet._cell_data.get("rowData") is not None


# ----------------------------------------------------------------------------
# Cell.value setter
# ----------------------------------------------------------------------------

class TestCellValueSetter:
    """`Cell.value = ...` must not poison the local cache. For formulas in
    particular, the effective + formatted values are unknown until refresh —
    old code stored `str(Formula(...))` ("Formula(=A1+1)") as
    `formattedValue`."""

    def test_literal_updates_formatted_value(self):
        data = _sheet_data(row_data=[_row("a")])
        cell = _make_spreadsheet(data).sheets[0].cell(0, 0)
        cell.value = 42
        assert cell.user_entered_value == 42
        assert cell.effective_value == 42
        assert cell.formatted_value == "42"

    def test_formula_invalidates_local_cache(self):
        # After setting a formula, we don't know what the spreadsheet will
        # compute — formatted_value / effective_value should be honest empties,
        # not the broken "Formula(=...)" string from str(Formula).
        data = _sheet_data(row_data=[_row("a")])
        cell = _make_spreadsheet(data).sheets[0].cell(0, 0)
        cell.value = Formula("=1+1")
        assert cell.user_entered_value == Formula("=1+1") or (
            isinstance(cell.user_entered_value, Formula)
            and cell.user_entered_value.text == "=1+1"
        )
        assert cell.formatted_value == ""
        assert cell.effective_value is None

    def test_hyperlink_formula_does_not_corrupt_formatted(self):
        data = _sheet_data(row_data=[_row("a")])
        cell = _make_spreadsheet(data).sheets[0].cell(0, 0)
        cell.value = HyperlinkFormula("https://example.com", "click")
        # Old code wrote str(HyperlinkFormula) → "Formula(=HYPERLINK(...))" as
        # formattedValue. Now the formatted_value should be empty (we don't
        # know the rendered link text until refresh).
        assert cell.formatted_value == ""

    def test_setting_string_value(self):
        data = _sheet_data(row_data=[_row("a")])
        cell = _make_spreadsheet(data).sheets[0].cell(0, 0)
        cell.value = "hello"
        assert cell.formatted_value == "hello"
        assert cell.effective_value == "hello"


# ----------------------------------------------------------------------------
# DeveloperMetadata.__delitem__
# ----------------------------------------------------------------------------

class TestMetadataDelete:
    """Deleting metadata must also shrink the local cache — otherwise
    iteration / len / contains report the deleted entry indefinitely."""

    def _make_with_metadata(self, items: list[dict[str, Any]]) -> Spreadsheet:
        data = _sheet_data(row_data=[_row("a")])
        data["developerMetadata"] = items
        return _make_spreadsheet(data)

    def test_delete_shrinks_local_list(self):
        ss = self._make_with_metadata([
            {"metadataId": 1, "metadataKey": "k1", "metadataValue": "v1"},
            {"metadataId": 2, "metadataKey": "k2", "metadataValue": "v2"},
        ])
        assert len(ss.metadata) == 2
        del ss.metadata[0]
        assert len(ss.metadata) == 1
        # The remaining item is the one that wasn't deleted.
        assert ss.metadata[0].key == "k2"

    def test_delete_iterates_correctly_after(self):
        ss = self._make_with_metadata([
            {"metadataId": 1, "metadataKey": "k1", "metadataValue": "v1"},
            {"metadataId": 2, "metadataKey": "k2", "metadataValue": "v2"},
            {"metadataId": 3, "metadataKey": "k3", "metadataValue": "v3"},
        ])
        del ss.metadata[1]
        keys = [m.key for m in ss.metadata]
        assert keys == ["k1", "k3"]

    def test_delete_all(self):
        ss = self._make_with_metadata([
            {"metadataId": 1, "metadataKey": "k1", "metadataValue": "v1"},
        ])
        del ss.metadata[0]
        assert len(ss.metadata) == 0
