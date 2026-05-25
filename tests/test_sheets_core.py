"""
Unit tests for core `gservices.sheets` behaviors — `Sheet`, `Row`, `Column`,
`Cell`, and `DeveloperMetadata`. These exercise the parts of the wrapper that
don't depend on the snapshot pipeline.
"""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

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


# ----------------------------------------------------------------------------
# _handle_row_inserted — cache freshness when rowData is missing
# ----------------------------------------------------------------------------

class TestRowInsertedCacheFreshness:
    """`_handle_row_inserted` used `_cell_data.get("rowData", []).insert(...)`,
    which mutates a throwaway list when the key is missing. The local cache
    then disagreed with what the spreadsheet actually had after the insert."""

    def test_insert_creates_rowdata_when_missing(self):
        # Build a sheet with _cell_data set but no "rowData" key.
        data = _sheet_data(row_data=[])
        ss = _make_spreadsheet(data)
        sheet = ss.sheets[0]
        assert sheet._cell_data is not None
        sheet._cell_data.pop("rowData", None)
        sheet._cell_data.pop("rowMetadata", None)

        sheet.rows.insert(before=0)

        # The mutation must have landed on the real _cell_data, not a throwaway.
        row_data = sheet._cell_data.get("rowData")
        assert row_data == [{"values": []}]
        row_meta = sheet._cell_data.get("rowMetadata")
        assert row_meta == [{}]

    def test_insert_preserves_existing_rowdata(self):
        data = _sheet_data(
            row_data=[_row("existing")],
            row_meta=[{"pixelSize": 30}],
        )
        sheet = _make_spreadsheet(data).sheets[0]
        sheet.rows.insert(before=0)
        assert sheet._cell_data is not None
        row_data = cast(list[dict[str, Any]], sheet._cell_data.get("rowData"))
        assert row_data is not None
        assert len(row_data) == 2
        # The inserted row is empty; the existing row's content is preserved.
        assert row_data[0] == {"values": []}
        assert row_data[1]["values"][0]["userEnteredValue"] == {
            "stringValue": "existing"
        }


# ----------------------------------------------------------------------------
# Columns.__getitem__ — caching
# ----------------------------------------------------------------------------

class TestColumnsCaching:
    """`Columns.__getitem__` used to return a fresh `Column` every call, so
    `col.metadata.add(...)` mutated a throwaway `_metadata`. Now identical."""

    def test_same_column_instance_returned(self):
        data = _sheet_data(row_data=[_row("a", "b", "c")])
        cols = _make_spreadsheet(data).sheets[0].columns
        assert cols[1] is cols[1]
        assert cols[0] is not cols[1]

    def test_metadata_persists_across_access(self):
        # Cached metadata mutation must survive a re-fetch through Columns.
        data = _sheet_data(
            row_data=[_row("a", "b")],
            col_meta=[
                {"developerMetadata": []},
                {"developerMetadata": []},
            ],
        )
        cols = _make_spreadsheet(data).sheets[0].columns
        cols[0].metadata.add("k", "v")
        # Same object after the add — and the entry is visible.
        assert len(cols[0].metadata) == 1
        assert cols[0].metadata[0].key == "k"

    def test_column_insert_shifts_cached_columns(self):
        data = _sheet_data(
            row_data=[_row("a", "b", "c")],
            col_meta=[{}, {}, {}],
        )
        cols = _make_spreadsheet(data).sheets[0].columns
        col0 = cols[0]
        col2 = cols[2]
        assert col0.index == 0
        assert col2.index == 2

        cols.insert(before=1)

        # col0 stays at index 0; col2 has shifted to index 3.
        assert col0.index == 0
        assert col2.index == 3
        # Re-fetching the same Column at its new index returns the same object.
        assert cols[3] is col2
        assert cols[0] is col0


# ----------------------------------------------------------------------------
# Column navigation / mutation — symmetry with Row
# ----------------------------------------------------------------------------

class TestColumnNavigation:
    """`Column.previous_column` / `next_column` should mirror Row's API."""

    def test_previous_column(self):
        data = _sheet_data(row_data=[_row("a", "b", "c")])
        cols = _make_spreadsheet(data).sheets[0].columns
        assert cols[0].previous_column is None
        assert cols[1].previous_column is cols[0]
        assert cols[2].previous_column is cols[1]

    def test_next_column(self):
        data = _sheet_data(row_data=[_row("a", "b", "c")])
        cols = _make_spreadsheet(data).sheets[0].columns
        assert cols[0].next_column is cols[1]
        assert cols[1].next_column is cols[2]
        assert cols[2].next_column is None

    def test_repr(self):
        data = _sheet_data(row_data=[_row("a", "b")])
        cols = _make_spreadsheet(data).sheets[0].columns
        assert "index=1" in repr(cols[1])


class TestColumnRemoveMove:
    """`Column.remove()` and `Column.move()` should mirror Row's API."""

    def test_remove_shifts_remaining_columns(self):
        data = _sheet_data(
            row_data=[_row("a", "b", "c", "d")],
            col_meta=[{}, {}, {}, {}],
        )
        sheet = _make_spreadsheet(data).sheets[0]
        cols = sheet.columns
        col_b = cols[1]
        col_d = cols[3]

        col_b.remove()

        # Original column 'd' has shifted from index 3 → index 2.
        assert col_d.index == 2
        assert cols[2] is col_d
        # Length decremented.
        assert len(cols) == 3

    def test_remove_makes_column_unusable(self):
        # Mirrors Row's behavior: after remove(), the Column should not be
        # used. Today's sentinel is `_sheet = None`, so subsequent attribute
        # access on `_sheet` blows up — PR 5 will replace with a clear error.
        data = _sheet_data(
            row_data=[_row("a", "b")],
            col_meta=[{}, {}],
        )
        col = _make_spreadsheet(data).sheets[0].columns[0]
        col.remove()
        # Accessing _sheet-dependent properties should not silently succeed.
        with pytest.raises((AttributeError, TypeError)):
            _ = col.width

    def test_move_forward(self):
        data = _sheet_data(
            row_data=[_row("a", "b", "c", "d")],
            col_meta=[{}, {}, {}, {}],
        )
        cols = _make_spreadsheet(data).sheets[0].columns
        col_a = cols[0]
        col_b = cols[1]
        col_c = cols[2]

        # Move column A to position 2 (after B). Result order: B, A, C, D
        # (gap closes on the left, then 'A' lands at index 1).
        col_a.move(index=2)

        assert col_a.index == 1
        assert col_b.index == 0
        assert col_c.index == 2

    def test_move_backward(self):
        data = _sheet_data(
            row_data=[_row("a", "b", "c", "d")],
            col_meta=[{}, {}, {}, {}],
        )
        cols = _make_spreadsheet(data).sheets[0].columns
        col_a = cols[0]
        col_d = cols[3]

        # Move column D to position 0 (in front). Result order: D, A, B, C.
        col_d.move(index=0)

        assert col_d.index == 0
        assert col_a.index == 1


# ----------------------------------------------------------------------------
# Columns container — iteration and sort
# ----------------------------------------------------------------------------

class TestColumnsIter:
    def test_iter_yields_all_columns(self):
        data = _sheet_data(row_data=[_row("a", "b", "c")])
        cols = _make_spreadsheet(data).sheets[0].columns
        seen = list(cols)
        assert len(seen) == 3
        assert seen[0].index == 0
        assert seen[1].index == 1
        assert seen[2].index == 2


class TestColumnsSort:
    def test_sort_by_column_index(self):
        # Sort by negated index to reverse the columns deterministically.
        data = _sheet_data(
            row_data=[_row("a", "b", "c")],
            col_meta=[{}, {}, {}],
        )
        cols = _make_spreadsheet(data).sheets[0].columns
        col_at_0 = cols[0]
        col_at_2 = cols[2]
        # Build the key from index so each column has a distinct sort key.
        cols.sort(lambda c: -c.index)
        # After sort, what was at index 2 → 0, what was at 0 → 2.
        assert col_at_2.index == 0
        assert col_at_0.index == 2


# ----------------------------------------------------------------------------
# Rows.insert — reconciled signature, now accepts int | Row
# ----------------------------------------------------------------------------

class TestRowsInsertSignature:
    """`Rows.insert` should accept `int | Row` for both `before` and `after`,
    matching `Columns.insert`."""

    def test_insert_before_row_object(self):
        data = _sheet_data(
            row_data=[_row("a"), _row("b")],
            row_meta=[{}, {}],
        )
        rows = _make_spreadsheet(data).sheets[0].rows
        target = rows[1]
        new_row = rows.insert(before=target)
        assert new_row.index == 1
        assert target.index == 2

    def test_insert_after_row_object(self):
        data = _sheet_data(
            row_data=[_row("a"), _row("b")],
            row_meta=[{}, {}],
        )
        rows = _make_spreadsheet(data).sheets[0].rows
        target = rows[0]
        new_row = rows.insert(after=target)
        assert new_row.index == 1
