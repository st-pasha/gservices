"""
Unit tests for core `gservices.sheets` behaviors — `Sheet`, `Row`, `Column`,
`Cell`, and `DeveloperMetadata`. These exercise the parts of the wrapper that
don't depend on the snapshot pipeline.
"""

import datetime as dt
import json
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from gservices.sheets.cell_value import Formula, HyperlinkFormula
from gservices.sheets.spreadsheet import Spreadsheet, SpreadsheetVersionMismatchError

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
        # After remove(), any property that reads from the underlying sheet
        # should raise a clear RuntimeError.
        data = _sheet_data(
            row_data=[_row("a", "b")],
            col_meta=[{}, {}],
        )
        col = _make_spreadsheet(data).sheets[0].columns[0]
        col.remove()
        with pytest.raises(RuntimeError, match="removed"):
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


# ----------------------------------------------------------------------------
# Spreadsheet.add_sheet — server ID reconciliation + property pass-through
# ----------------------------------------------------------------------------

class TestAddSheet:
    """`add_sheet` queues an `addSheet` request and must reconcile the
    server-assigned sheetId via response callback. Also pass-through for
    rowCount / columnCount / hideGridlines / tabColorStyle."""

    def _make_with_mock_service(
        self, batch_replies: list[dict[str, Any]]
    ) -> tuple[Spreadsheet, MagicMock]:
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [{
                "properties": {
                    "sheetId": 0,
                    "title": "Initial",
                    "index": 0,
                    "gridProperties": {"rowCount": 100, "columnCount": 26},
                },
                "data": [],
            }],
        }
        service = MagicMock()
        # Stub the batchUpdate response so save() can iterate replies.
        spreadsheets = service.resource.spreadsheets.return_value
        spreadsheets.batchUpdate.return_value.execute.return_value = {
            "replies": batch_replies,
        }
        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        return ss, service

    def _captured_request(self, service: MagicMock) -> dict[str, Any]:
        """Pull the single request body out of the captured batchUpdate call."""
        spreadsheets = service.resource.spreadsheets.return_value
        call_kwargs = spreadsheets.batchUpdate.call_args.kwargs
        body = call_kwargs["body"]
        requests = body["requests"]
        assert len(requests) == 1
        return requests[0]

    def test_add_sheet_queues_request_with_name(self):
        ss, service = self._make_with_mock_service(
            [{"addSheet": {"properties": {"sheetId": 1, "title": "X"}}}]
        )
        new_sheet = ss.add_sheet("X")
        assert new_sheet.title == "X"
        # Local heuristic ID (max existing + 1) — before save().
        assert new_sheet.id == 1

        ss.save()

        # batchUpdate was called with one addSheet request.
        request = self._captured_request(service)
        assert "addSheet" in request
        properties = request["addSheet"]["properties"]
        assert properties["title"] == "X"

    def test_add_sheet_reconciles_server_assigned_id(self):
        # The server may assign a different ID than our heuristic (max+1).
        # The response callback must patch the Sheet.id to the real one.
        ss, _ = self._make_with_mock_service(
            [{"addSheet": {"properties": {"sheetId": 42, "title": "X"}}}]
        )
        new_sheet = ss.add_sheet("X")
        assert new_sheet.id == 1  # heuristic before save

        ss.save()

        assert new_sheet.id == 42  # reconciled with server response
        # And resolving by name still returns the same Sheet object.
        assert ss.sheet("X") is new_sheet

    def test_add_sheet_passes_row_and_column_count(self):
        ss, service = self._make_with_mock_service(
            [{"addSheet": {"properties": {"sheetId": 1, "title": "Y"}}}]
        )
        ss.add_sheet("Y", row_count=50, column_count=10)
        ss.save()

        properties = self._captured_request(service)["addSheet"]["properties"]
        grid = properties.get("gridProperties", {})
        assert grid["rowCount"] == 50
        assert grid["columnCount"] == 10

    def test_add_sheet_passes_hide_gridlines(self):
        ss, service = self._make_with_mock_service(
            [{"addSheet": {"properties": {"sheetId": 1, "title": "Z"}}}]
        )
        ss.add_sheet("Z", hide_gridlines=True)
        ss.save()

        properties = self._captured_request(service)["addSheet"]["properties"]
        assert properties.get("gridProperties", {}).get("hideGridlines") is True

    def test_add_sheet_passes_tab_color(self):
        ss, service = self._make_with_mock_service(
            [{"addSheet": {"properties": {"sheetId": 1, "title": "C"}}}]
        )
        ss.add_sheet("C", tab_color="#ff8800")
        ss.save()

        properties = self._captured_request(service)["addSheet"]["properties"]
        tab_color = properties.get("tabColorStyle", {})
        assert "rgbColor" in tab_color

    def test_add_sheet_with_no_existing_sheets(self):
        # max(...) on an empty iterable would crash without a default.
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [],
        }
        service = MagicMock()
        service.resource.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
            "replies": [{"addSheet": {"properties": {"sheetId": 1, "title": "First"}}}],
        }
        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        new_sheet = ss.add_sheet("First")
        # Before save: heuristic 0 (max of empty default=-1, plus 1).
        assert new_sheet.id == 0
        ss.save()
        # After save: server-assigned 1.
        assert new_sheet.id == 1


# ----------------------------------------------------------------------------
# Spreadsheet.delete_sheet — parameter rebinding cleanup
# ----------------------------------------------------------------------------

class TestDeleteSheet:
    def test_delete_by_name(self):
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "A", "index": 0}, "data": []},
                {"properties": {"sheetId": 1, "title": "B", "index": 1}, "data": []},
            ],
        }
        ss = _make_spreadsheet(data)
        ss.delete_sheet("A")
        assert [s.title for s in ss.sheets] == ["B"]

    def test_delete_by_object(self):
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "A", "index": 0}, "data": []},
                {"properties": {"sheetId": 1, "title": "B", "index": 1}, "data": []},
            ],
        }
        ss = _make_spreadsheet(data)
        ss.delete_sheet(ss.sheets[0])
        assert [s.title for s in ss.sheets] == ["B"]

    def test_delete_missing_raises_keyerror(self):
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "A", "index": 0}, "data": []},
            ],
        }
        ss = _make_spreadsheet(data)
        with pytest.raises(KeyError):
            ss.delete_sheet("Nonexistent")


# ----------------------------------------------------------------------------
# CellFormat — border edge dedup
# ----------------------------------------------------------------------------

class TestCellFormatBorders:
    """The four border edges (top/right/bottom/left) used to be near-duplicate
    getter+setter quartets; they share a `_get_border` / `_set_border` helper
    now. The four public properties must continue to behave the same."""

    def _cell(self, border_data: dict[str, Any] | None = None) -> Any:
        cell_data: dict[str, Any] = {
            "userEnteredValue": {"stringValue": "x"},
            "effectiveFormat": {"textFormat": {}},
        }
        if border_data is not None:
            cell_data["effectiveFormat"]["borders"] = border_data
        data = _sheet_data(row_data=[{"values": [cell_data]}])
        return _make_spreadsheet(data).sheets[0].cell(0, 0)

    def test_get_each_edge(self):
        cell = self._cell({
            "top": {"style": "SOLID", "width": 1},
            "right": {"style": "DASHED", "width": 2},
            "bottom": {"style": "DOTTED", "width": 1},
            "left": {"style": "SOLID_THICK", "width": 3},
        })
        assert cell.format.border_top is not None
        assert cell.format.border_top.style == "SOLID"
        assert cell.format.border_right is not None
        assert cell.format.border_right.style == "DASHED"
        assert cell.format.border_bottom is not None
        assert cell.format.border_bottom.style == "DOTTED"
        assert cell.format.border_left is not None
        assert cell.format.border_left.style == "SOLID_THICK"

    def test_get_missing_edge_returns_none(self):
        cell = self._cell()
        assert cell.format.border_top is None
        assert cell.format.border_right is None
        assert cell.format.border_bottom is None
        assert cell.format.border_left is None

    def test_set_each_edge(self):
        from gservices.sheets.border_format import BorderFormat
        cell = self._cell()
        cell.format.border_top = BorderFormat(style="SOLID", width=1, color="#ff0000")
        cell.format.border_right = BorderFormat(style="DASHED", width=2)
        cell.format.border_bottom = BorderFormat(style="DOTTED", width=1)
        cell.format.border_left = BorderFormat(style="SOLID_THICK", width=3)

        # The local data structure should reflect each set independently.
        borders = cell.format._data.get("borders", {})
        assert borders["top"]["style"] == "SOLID"
        assert borders["right"]["style"] == "DASHED"
        assert borders["bottom"]["style"] == "DOTTED"
        assert borders["left"]["style"] == "SOLID_THICK"

    def test_clear_edge_by_assigning_none(self):
        cell = self._cell({"top": {"style": "SOLID", "width": 1}})
        cell.format.border_top = None
        # Assigning None collapses the border to no-style / no-width.
        border = cell.format.border_top
        assert border is None or (border.style is None and border.width == 0)


# ----------------------------------------------------------------------------
# Cell.format setter — accepts the CellFormat wrapper
# ----------------------------------------------------------------------------

class TestCellFormatAssignment:
    """`cell.format = other_cell.format` should work: setter now accepts both
    the raw `gs.CellFormat` dict and the `CellFormat` wrapper."""

    def test_assign_wrapper(self):
        # Source cell has bold red text; target has default. After assignment,
        # the target's userEnteredFormat should contain the same fields.
        data = _sheet_data(row_data=[
            {"values": [
                {
                    "userEnteredValue": {"stringValue": "src"},
                    "effectiveFormat": {
                        "textFormat": {"bold": True, "fontSize": 14},
                    },
                },
                {"userEnteredValue": {"stringValue": "dst"}},
            ]},
        ])
        sheet = _make_spreadsheet(data).sheets[0]
        src = sheet.cell(0, 0)
        dst = sheet.cell(0, 1)
        dst.format = src.format
        # Now the destination's userEnteredFormat matches the source's data.
        assert dst._data.get("userEnteredFormat") is not None


# ----------------------------------------------------------------------------
# Row.remove() sentinel
# ----------------------------------------------------------------------------

class TestRowRemoveSentinel:
    """After `Row.remove()`, attribute access should raise a clear
    RuntimeError, mirroring `Column.remove()`."""

    def test_removed_row_raises(self):
        data = _sheet_data(
            row_data=[_row("a"), _row("b")],
            row_meta=[{}, {}],
        )
        row = _make_spreadsheet(data).sheets[0].rows[0]
        row.remove()
        with pytest.raises(RuntimeError, match="removed"):
            _ = row.height

    def test_removed_column_raises_on_metadata_access(self):
        data = _sheet_data(
            row_data=[_row("a", "b")],
            col_meta=[{}, {}],
        )
        col = _make_spreadsheet(data).sheets[0].columns[0]
        col.remove()
        with pytest.raises(RuntimeError, match="removed"):
            _ = col.metadata


# ----------------------------------------------------------------------------
# Spreadsheet.save(check_version=True) — concurrent-edit detection
# ----------------------------------------------------------------------------

class TestSaveCheckVersion:
    """Open with `track_version=True` and `save(check_version=True)` to detect
    third-party edits between load and save."""

    def _ss_with_version_tracking(
        self,
        *,
        drive_versions: list[str],
        batch_replies: list[dict[str, Any]] | None = None,
    ) -> tuple[Spreadsheet, MagicMock]:
        """Build a Spreadsheet whose Drive resource returns `drive_versions`
        on successive `.files().get(...).execute()` calls. Optional
        `batch_replies` configure batchUpdate responses."""
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [{
                "properties": {"sheetId": 0, "title": "S", "index": 0},
                "data": [],
            }],
        }
        service = MagicMock()
        # Drive resource — successive .execute() calls cycle through the list.
        drive_get_execute = (
            service._google.Drive.resource.files.return_value.get
            .return_value.execute
        )
        drive_get_execute.side_effect = [{"version": v} for v in drive_versions]
        # Sheets batchUpdate — minimal response that doesn't error out save().
        sheets_batch_execute = (
            service.resource.spreadsheets.return_value.batchUpdate
            .return_value.execute
        )
        sheets_batch_execute.return_value = {"replies": batch_replies or []}
        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        # Simulate `Sheets.open(track_version=True)` capturing the baseline.
        ss._baseline_version = ss._fetch_drive_version()
        return ss, service

    def test_check_version_without_tracking_raises(self):
        # No track_version=True, so _baseline_version is None — using
        # check_version=True is a programming error.
        data = _sheet_data(row_data=[_row("a")])
        ss = _make_spreadsheet(data)
        # Queue a no-op-ish change so save() actually runs past the early-out.
        ss._add_request({"updateCells": {"rows": [], "fields": "userEnteredValue"}})
        with pytest.raises(ValueError, match="track_version"):
            ss.save(check_version=True)

    def test_save_unchanged_version_succeeds(self):
        # Baseline=10, current=10 → no mismatch. Then post-save version
        # refresh returns 11 (our own write bumped it).
        ss, _ = self._ss_with_version_tracking(
            drive_versions=["10", "10", "11"],
        )
        assert ss._baseline_version == 10
        ss._add_request({"updateCells": {"rows": [], "fields": "userEnteredValue"}})
        ss.save(check_version=True)
        # Baseline has been refreshed to the post-save value.
        assert ss._baseline_version == 11

    def test_save_changed_version_raises(self):
        # Baseline=10, current=11 → someone else edited the file. Raise
        # before sending any batchUpdate.
        ss, service = self._ss_with_version_tracking(
            drive_versions=["10", "11"],
        )
        ss._add_request({"updateCells": {"rows": [], "fields": "userEnteredValue"}})
        with pytest.raises(SpreadsheetVersionMismatchError) as exc:
            ss.save(check_version=True)
        assert exc.value.baseline == 10
        assert exc.value.current == 11
        # And no batchUpdate was sent.
        batch_call = service.resource.spreadsheets.return_value.batchUpdate
        assert batch_call.call_count == 0
        # Pending updates remain queued so the user can decide what to do.
        assert len(ss._pending_updates) == 1

    def test_unchecked_save_still_refreshes_baseline_when_tracking(self):
        # If tracking is enabled but the user calls save() without
        # check_version=True, baseline still refreshes after a successful
        # save so future checked saves don't spuriously mismatch.
        ss, _ = self._ss_with_version_tracking(
            drive_versions=["5", "6"],
        )
        ss._add_request({"updateCells": {"rows": [], "fields": "userEnteredValue"}})
        ss.save()  # unchecked
        assert ss._baseline_version == 6

    def test_save_without_pending_updates_short_circuits(self):
        # No pending updates → save() is a no-op even with check_version.
        # The version check still fires (to catch desync state), but no
        # batchUpdate is sent.
        ss, service = self._ss_with_version_tracking(
            drive_versions=["10", "10"],
        )
        ss.save(check_version=True)
        batch_call = service.resource.spreadsheets.return_value.batchUpdate
        assert batch_call.call_count == 0


# ----------------------------------------------------------------------------
# Spreadsheet.exclusive_edit — protected-range locking
# ----------------------------------------------------------------------------

class TestExclusiveEdit:
    """`exclusive_edit()` brackets a block with addProtectedRange /
    deleteProtectedRange. Encodes TTL metadata in the protection
    description so a crashed run can be cleaned up by the next caller."""

    def _ss_with_protection_mock(
        self,
        *,
        existing_protections: list[dict[str, Any]] | None = None,
        sheet_ids: list[int] | None = None,
        new_protection_ids: list[int] | None = None,
        my_email: str = "me@example.com",
    ) -> tuple[Spreadsheet, MagicMock]:
        """Build a Spreadsheet whose service mocks:
          - drive.about().get → returns my_email
          - spreadsheets.get → returns existing protections
          - spreadsheets.batchUpdate → returns replies with new_protection_ids
            for addProtectedRange entries (cycled).
        """
        sheet_ids = sheet_ids if sheet_ids is not None else [0]
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {
                    "properties": {"sheetId": sid, "title": f"S{sid}", "index": i},
                    "data": [],
                }
                for i, sid in enumerate(sheet_ids)
            ],
        }
        service = MagicMock()

        # Drive about() → user email
        drive_about_execute = (
            service._google.Drive.resource.about.return_value.get
            .return_value.execute
        )
        drive_about_execute.return_value = {"user": {"emailAddress": my_email}}

        # spreadsheets.get → existing protections, scoped per sheet
        protections_by_sheet: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sheet_ids}
        for pr in (existing_protections or []):
            sid = pr.get("_sheet_id", sheet_ids[0])
            pr_copy = {k: v for k, v in pr.items() if k != "_sheet_id"}
            protections_by_sheet.setdefault(sid, []).append(pr_copy)
        get_response = {
            "sheets": [
                {
                    "properties": {"sheetId": sid},
                    "protectedRanges": protections_by_sheet[sid],
                }
                for sid in sheet_ids
            ],
        }
        sheets_get_execute = (
            service.resource.spreadsheets.return_value.get
            .return_value.execute
        )
        sheets_get_execute.return_value = get_response

        # spreadsheets.batchUpdate → return new protectedRangeIds for each
        # addProtectedRange request in the batch.
        new_ids_iter = iter(new_protection_ids or [100, 101, 102, 103])

        def make_reply_for(body: dict[str, Any]) -> dict[str, Any]:
            replies: list[dict[str, Any]] = []
            for req in body.get("requests", []):
                if "addProtectedRange" in req:
                    replies.append({
                        "addProtectedRange": {
                            "protectedRange": {"protectedRangeId": next(new_ids_iter)}
                        }
                    })
                else:
                    replies.append({})
            return {"replies": replies}

        # batchUpdate execute needs to inspect the body each call.
        batch_call = service.resource.spreadsheets.return_value.batchUpdate

        def batch_update(*, spreadsheetId: str, body: dict[str, Any]) -> MagicMock:
            execute = MagicMock()
            execute.execute.return_value = make_reply_for(body)
            return execute

        batch_call.side_effect = batch_update

        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        return ss, service

    def _batch_calls(self, service: MagicMock) -> list[dict[str, Any]]:
        """Return the request bodies of every batchUpdate call, in order."""
        batch = service.resource.spreadsheets.return_value.batchUpdate
        return [c.kwargs["body"] for c in batch.call_args_list]

    def test_basic_acquire_and_release(self):
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0], new_protection_ids=[100],
        )
        with ss.exclusive_edit():
            pass

        bodies = self._batch_calls(service)
        # Two batchUpdates: acquire and release.
        assert len(bodies) == 2

        # First batch: one addProtectedRange request.
        acquire_requests = bodies[0]["requests"]
        assert len(acquire_requests) == 1
        protected = acquire_requests[0]["addProtectedRange"]["protectedRange"]
        assert protected["range"] == {"sheetId": 0}
        assert protected["editors"] == {"users": ["me@example.com"]}
        assert protected["description"].startswith("gservices-lock:")
        # Metadata is parseable JSON with expected keys.
        meta = json.loads(protected["description"][len("gservices-lock:"):])
        assert meta["holder"] == "me@example.com"
        assert "lock_id" in meta
        assert "expires_at" in meta

        # Second batch: deleteProtectedRange for our id 100.
        release_requests = bodies[1]["requests"]
        assert release_requests == [
            {"deleteProtectedRange": {"protectedRangeId": 100}}
        ]

    def test_release_on_exception(self):
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0], new_protection_ids=[100],
        )

        class _UserError(Exception):
            pass

        with pytest.raises(_UserError):
            with ss.exclusive_edit():
                raise _UserError()

        bodies = self._batch_calls(service)
        # Acquire + release, even though user code raised.
        assert len(bodies) == 2
        # Release batch present.
        assert bodies[1]["requests"] == [
            {"deleteProtectedRange": {"protectedRangeId": 100}}
        ]

    def test_stale_lock_cleanup(self):
        # A previous lock has expires_at well in the past — current caller
        # should add a deleteProtectedRange for it in the acquire batch.
        past = (
            dt.datetime.now(dt.UTC) - dt.timedelta(seconds=600)
        ).isoformat()
        stale_description = "gservices-lock:" + json.dumps({
            "holder": "ghost@example.com",
            "lock_id": "ghost-uuid",
            "expires_at": past,
        })
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0],
            existing_protections=[
                {
                    "_sheet_id": 0,
                    "protectedRangeId": 99,
                    "description": stale_description,
                },
            ],
            new_protection_ids=[100],
        )
        with ss.exclusive_edit():
            pass

        acquire_requests = self._batch_calls(service)[0]["requests"]
        # Delete-99 comes before our add (single batch, atomic on server).
        assert acquire_requests[0] == {
            "deleteProtectedRange": {"protectedRangeId": 99}
        }
        assert "addProtectedRange" in acquire_requests[1]

    def test_fresh_lock_not_cleaned_up(self):
        # A non-stale lock from another holder must be left alone — even
        # though we may end up coexisting (two locks active). This is the
        # documented best-effort behavior.
        future = (
            dt.datetime.now(dt.UTC) + dt.timedelta(seconds=600)
        ).isoformat()
        fresh_description = "gservices-lock:" + json.dumps({
            "holder": "other@example.com",
            "lock_id": "other-uuid",
            "expires_at": future,
        })
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0],
            existing_protections=[
                {
                    "_sheet_id": 0,
                    "protectedRangeId": 99,
                    "description": fresh_description,
                },
            ],
            new_protection_ids=[100],
        )
        with ss.exclusive_edit():
            pass

        acquire_requests = self._batch_calls(service)[0]["requests"]
        # No delete of 99 in the acquire batch — just our add.
        assert all(
            r.get("deleteProtectedRange", {}).get("protectedRangeId") != 99
            for r in acquire_requests
        )

    def test_non_lock_protection_not_touched(self):
        # A user-created protection (no gservices-lock: prefix) must be
        # ignored entirely — stale-lock cleanup is scoped to our marker.
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0],
            existing_protections=[
                {
                    "_sheet_id": 0,
                    "protectedRangeId": 77,
                    "description": "User's protected header row",
                },
            ],
            new_protection_ids=[100],
        )
        with ss.exclusive_edit():
            pass

        acquire_requests = self._batch_calls(service)[0]["requests"]
        # No delete of 77; just our add.
        for r in acquire_requests:
            assert r.get("deleteProtectedRange", {}).get("protectedRangeId") != 77

    def test_sheets_parameter_scopes_protection(self):
        # Pass an explicit list — only those sheets get protected.
        ss, service = self._ss_with_protection_mock(
            sheet_ids=[0, 1, 2],
            new_protection_ids=[100, 101],
        )
        # Lock only the first two sheets.
        target_sheets = list(ss.sheets[0:2])
        with ss.exclusive_edit(sheets=target_sheets):
            pass

        acquire_requests = self._batch_calls(service)[0]["requests"]
        adds = [r for r in acquire_requests if "addProtectedRange" in r]
        assert len(adds) == 2
        protected_sheet_ids = sorted(
            r["addProtectedRange"]["protectedRange"]["range"]["sheetId"]
            for r in adds
        )
        assert protected_sheet_ids == [0, 1]


# ----------------------------------------------------------------------------
# Spreadsheet.reload — refresh loaded sheet data from server
# ----------------------------------------------------------------------------

class TestReload:
    """`reload()` invalidates and re-fetches cell data for sheets that
    had data loaded. Sheets that were never loaded stay untouched."""

    def _ss_with_two_sheets_and_reload_mock(
        self,
        initial_value: str,
        refreshed_value: str,
    ) -> tuple[Spreadsheet, MagicMock]:
        """Build a Spreadsheet whose sheets[0] is pre-loaded with
        `initial_value` at A1, and whose `spreadsheets.get(...).execute()`
        returns `refreshed_value` at A1 on the next call."""
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {
                    "properties": {"sheetId": 0, "title": "S0", "index": 0},
                    "data": [{
                        "rowData": [{
                            "values": [{
                                "userEnteredValue": {"stringValue": initial_value},
                            }],
                        }],
                        "rowMetadata": [{}],
                        "columnMetadata": [{}],
                    }],
                },
                # Second sheet: not loaded (data: []).
                {
                    "properties": {"sheetId": 1, "title": "S1", "index": 1},
                    "data": [],
                },
            ],
        }
        service = MagicMock()
        sheets_get_execute = (
            service.resource.spreadsheets.return_value.get
            .return_value.execute
        )
        # Refresh response: contains both sheets in the response, but only
        # the loaded one (sheetId=0) gets updated by _load_all_data.
        sheets_get_execute.return_value = {
            "sheets": [
                {
                    "properties": {"sheetId": 0},
                    "data": [{
                        "rowData": [{
                            "values": [{
                                "userEnteredValue": {
                                    "stringValue": refreshed_value
                                },
                            }],
                        }],
                        "rowMetadata": [{}],
                        "columnMetadata": [{}],
                    }],
                },
            ],
        }
        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        return ss, service

    def test_reload_refreshes_loaded_sheet(self):
        ss, _ = self._ss_with_two_sheets_and_reload_mock(
            initial_value="before", refreshed_value="after",
        )
        # Pre-reload: original value visible.
        assert ss.sheets[0].cell(0, 0).user_entered_value == "before"
        ss.reload()
        # Post-reload: refreshed value visible. Note: must re-fetch the cell
        # via sheet.cell() — old Cell references are stale.
        assert ss.sheets[0].cell(0, 0).user_entered_value == "after"

    def test_reload_does_not_eager_load_unloaded_sheets(self):
        ss, _ = self._ss_with_two_sheets_and_reload_mock(
            initial_value="x", refreshed_value="x",
        )
        # sheets[1] starts unloaded.
        assert ss.sheets[1]._cell_data is None
        ss.reload()
        # Still unloaded after reload — reload should NOT eager-fetch.
        assert ss.sheets[1]._cell_data is None

    def test_reload_with_no_loaded_sheets_short_circuits(self):
        # If nothing was loaded, reload() is a no-op (no API call).
        data: dict[str, Any] = {
            "spreadsheetId": "TEST",
            "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "S0", "index": 0}, "data": []},
            ],
        }
        service = MagicMock()
        ss = Spreadsheet(cast("gs.Spreadsheet", data), service)
        ss.reload()
        # spreadsheets.get was never called.
        assert service.resource.spreadsheets.return_value.get.call_count == 0

    def test_reload_clears_cell_cache(self):
        ss, _ = self._ss_with_two_sheets_and_reload_mock(
            initial_value="before", refreshed_value="after",
        )
        old_cell = ss.sheets[0].cell(0, 0)
        ss.reload()
        new_cell = ss.sheets[0].cell(0, 0)
        # Different Cell object (cache was cleared).
        assert new_cell is not old_cell
        # New cell sees fresh data; old cell points at the orphaned dict.
        assert new_cell.user_entered_value == "after"
