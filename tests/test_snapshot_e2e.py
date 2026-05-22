"""
End-to-end tests for `Spreadsheet.snapshot()` and `save_snapshot()`.

These exercise the full builder pipeline on a hand-constructed `Spreadsheet`
object. They cover round-trip stability (the same input produces byte-identical
snapshots), layered independence (changing a value diffs only `data`; changing
a format diffs only `formats`), and the public `save_snapshot()` writing
contract (valid JSON, expected per-row layout, etc.).
"""

import copy
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from gservices.sheets.snapshot import SCHEMA_VERSION
from gservices.sheets.spreadsheet import Spreadsheet

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


# ----------------------------------------------------------------------------
# Fixture data builders
# ----------------------------------------------------------------------------

def _arial_10() -> dict[str, Any]:
    """Standard text format that matches the spreadsheet default."""
    return {"fontFamily": "Arial", "fontSize": 10}


def _cell(data: dict[str, Any], sheet_i: int, row_i: int, col_i: int) -> dict[str, Any]:
    """Return the (mutable) CellData dict at the given sheet/row/col position.

    Used by tests that mutate a deep-cloned API response — basedpyright can't
    follow the deeply-nested TypedDict chain, so we localize the cast.
    """
    sheet = cast(list[dict[str, Any]], data["sheets"])[sheet_i]
    rows = cast(list[dict[str, Any]], sheet["data"][0]["rowData"])
    values = cast(list[dict[str, Any]], rows[row_i]["values"])
    return values[col_i]


def _make_spreadsheet(data: dict[str, Any]) -> Spreadsheet:
    """Construct a Spreadsheet from a raw API-response dict with a mock service."""
    return Spreadsheet(cast("gs.Spreadsheet", data), MagicMock())


def _minimal_data() -> dict[str, Any]:
    """A 2x2 sheet: header row in bold, one data row."""
    return {
        "spreadsheetId": "MINIMAL",
        "properties": {
            "title": "Minimal",
            "locale": "en_US",
            "timeZone": "UTC",
            "defaultFormat": {"textFormat": _arial_10()},
        },
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Sheet1",
                    "index": 0,
                    "gridProperties": {"rowCount": 1000, "columnCount": 26},
                },
                "data": [
                    {
                        "rowData": [
                            {"values": [
                                {"userEnteredValue": {"stringValue": "Name"},
                                 "effectiveFormat": {
                                     "textFormat": {**_arial_10(), "bold": True}}},
                                {"userEnteredValue": {"stringValue": "Value"},
                                 "effectiveFormat": {
                                     "textFormat": {**_arial_10(), "bold": True}}},
                            ]},
                            {"values": [
                                {"userEnteredValue": {"stringValue": "Alice"},
                                 "effectiveFormat": {"textFormat": _arial_10()}},
                                {"userEnteredValue": {"numberValue": 42},
                                 "effectiveFormat": {"textFormat": _arial_10()}},
                            ]},
                        ],
                        "rowMetadata": [{}, {}],
                        "columnMetadata": [{}, {}],
                    }
                ],
            }
        ],
    }


def _rich_data() -> dict[str, Any]:
    """A sheet exercising every snapshot layer: merges, formulas, formats,
    borders, dates, notes, hyperlinks, computed values."""
    bold = {"textFormat": {**_arial_10(), "bold": True}}
    return {
        "spreadsheetId": "RICH",
        "properties": {
            "title": "Rich",
            "locale": "en_US",
            "timeZone": "America/New_York",
            "spreadsheetTheme": {
                "primaryFontFamily": "Arial",
                "themeColors": [
                    {"colorType": "ACCENT1",
                     "color": {"rgbColor": {"red": 0.26, "green": 0.52, "blue": 0.96}}}
                ],
            },
            "defaultFormat": {"textFormat": _arial_10()},
        },
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Summary",
                    "index": 0,
                    "gridProperties": {
                        "rowCount": 1000, "columnCount": 26, "frozenRowCount": 1,
                    },
                },
                "merges": [
                    {"startRowIndex": 0, "endRowIndex": 1,
                     "startColumnIndex": 0, "endColumnIndex": 3}
                ],
                "data": [
                    {
                        "rowData": [
                            {"values": [
                                {"userEnteredValue": {"stringValue": "Name"},
                                 "effectiveFormat": bold},
                                {"userEnteredValue": {"stringValue": "Date"},
                                 "effectiveFormat": bold},
                                {"userEnteredValue": {"stringValue": "Total"},
                                 "effectiveFormat": bold},
                            ]},
                            {"values": [
                                {"userEnteredValue": {"stringValue": "Alice"},
                                 "effectiveFormat": {"textFormat": _arial_10()},
                                 "hyperlink": "#gid=999"},
                                {"userEnteredValue": {"numberValue": 45458.0},
                                 "effectiveFormat": {
                                     "numberFormat": {"type": "DATE",
                                                      "pattern": "yyyy-mm-dd"},
                                     "textFormat": _arial_10()}},
                                {"userEnteredValue": {"formulaValue": "=42*2"},
                                 "effectiveValue": {"numberValue": 84},
                                 "effectiveFormat": {"textFormat": _arial_10()},
                                 "note": "Computed"},
                            ]},
                            {"values": [
                                {"userEnteredValue": {"stringValue": "Bob"},
                                 "effectiveFormat": {
                                     "textFormat": {**_arial_10(),
                                                    "strikethrough": True}}},
                                {"userEnteredValue": {"numberValue": 45459.0},
                                 "effectiveFormat": {
                                     "numberFormat": {"type": "DATE",
                                                      "pattern": "yyyy-mm-dd"},
                                     "textFormat": _arial_10()}},
                                {"userEnteredValue": {"numberValue": 100},
                                 "effectiveFormat": {
                                     "textFormat": _arial_10(),
                                     "borders": {
                                         "bottom": {"style": "SOLID", "width": 1,
                                                    "colorStyle": {"rgbColor": {}}}}}},
                            ]},
                        ],
                        "rowMetadata": [{"pixelSize": 32}, {}, {}],
                        "columnMetadata": [{"pixelSize": 180}, {}, {}],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def minimal_spreadsheet() -> Spreadsheet:
    return _make_spreadsheet(_minimal_data())


@pytest.fixture
def rich_spreadsheet() -> Spreadsheet:
    return _make_spreadsheet(_rich_data())


# ----------------------------------------------------------------------------
# Round-trip stability — the central correctness invariant
# ----------------------------------------------------------------------------

class TestRoundTripStability:
    def test_minimal_snapshot_is_deterministic(
        self, minimal_spreadsheet: Spreadsheet
    ):
        snap1 = minimal_spreadsheet.snapshot()
        snap2 = minimal_spreadsheet.snapshot()
        assert snap1 == snap2

    def test_rich_snapshot_is_deterministic(self, rich_spreadsheet: Spreadsheet):
        snap1 = rich_spreadsheet.snapshot()
        snap2 = rich_spreadsheet.snapshot()
        assert snap1 == snap2

    def test_save_snapshot_produces_byte_identical_output(
        self, rich_spreadsheet: Spreadsheet
    ):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "snap1.json"
            p2 = Path(d) / "snap2.json"
            rich_spreadsheet.save_snapshot(p1)
            rich_spreadsheet.save_snapshot(p2)
            assert p1.read_bytes() == p2.read_bytes()


# ----------------------------------------------------------------------------
# Batch-load: one API call per snapshot, not one per sheet
# ----------------------------------------------------------------------------

class TestBatchLoad:
    def _spreadsheet_metadata_only(self, sheet_count: int) -> dict[str, Any]:
        """Build a spreadsheet response with N sheets but NO grid data, as if
        opened with `load=False`. Each sheet's `_cell_data` will be None."""
        return {
            "spreadsheetId": "MULTI",
            "properties": {
                "title": "Multi", "locale": "en_US", "timeZone": "UTC",
                "defaultFormat": {"textFormat": _arial_10()},
            },
            "sheets": [
                {
                    "properties": {
                        "sheetId": i,
                        "title": f"S{i}",
                        "index": i,
                        "gridProperties": {"rowCount": 10, "columnCount": 5},
                    },
                    # Deliberately omit "data" so sheet._cell_data stays None.
                }
                for i in range(sheet_count)
            ],
        }

    def _service_returning_full_data(self, sheet_count: int) -> MagicMock:
        """Build a mock service whose `spreadsheets().get(...).execute()` returns
        a single dict containing grid data for every sheet."""
        full_response: dict[str, Any] = {
            "spreadsheetId": "MULTI",
            "sheets": [
                {
                    "properties": {"sheetId": i, "title": f"S{i}", "index": i},
                    "data": [{
                        "rowData": [{"values": [
                            {"userEnteredValue": {"numberValue": i}}
                        ]}],
                        "rowMetadata": [{}],
                        "columnMetadata": [{}],
                    }],
                }
                for i in range(sheet_count)
            ],
        }
        service = MagicMock()
        service.resource.spreadsheets.return_value.get.return_value.execute.return_value = (
            full_response
        )
        return service

    def test_snapshot_uses_one_api_call_for_many_sheets(self):
        # Construct a Spreadsheet manually so we control its service mock.
        from gservices.sheets.spreadsheet import Spreadsheet
        data = self._spreadsheet_metadata_only(sheet_count=5)
        service = self._service_returning_full_data(sheet_count=5)
        spreadsheet = Spreadsheet(cast("gs.Spreadsheet", data), service)

        spreadsheet.snapshot()

        # The fix-under-test: one call total to `.get()`, not five.
        get = service.resource.spreadsheets.return_value.get
        assert get.call_count == 1
        # And it requested includeGridData=True.
        _, kwargs = get.call_args
        assert kwargs.get("includeGridData") is True

    def test_skip_api_call_if_all_sheets_preloaded(
        self, minimal_spreadsheet: Spreadsheet
    ):
        # `minimal_spreadsheet` has `_cell_data` populated for its single sheet,
        # so no fetch should happen.
        minimal_spreadsheet.snapshot()
        service = minimal_spreadsheet._service  # type: ignore[reportPrivateUsage]
        get = cast(Any, service.resource.spreadsheets).return_value.get
        assert get.call_count == 0


# ----------------------------------------------------------------------------
# Schema-level structure
# ----------------------------------------------------------------------------

class TestSchema:
    def test_schema_version(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert snap["schema_version"] == SCHEMA_VERSION

    def test_spreadsheet_meta_present(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        meta = snap["spreadsheet"]
        assert meta.get("id") == "MINIMAL"
        assert meta.get("title") == "Minimal"
        assert meta.get("locale") == "en_US"

    def test_one_sheet(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert len(snap["sheets"]) == 1
        assert snap["sheets"][0].get("title") == "Sheet1"

    def test_top_level_key_order(self, rich_spreadsheet: Spreadsheet):
        # schema_version, then spreadsheet, then sheets — important for diff stability.
        snap = rich_spreadsheet.snapshot()
        assert list(snap.keys()) == ["schema_version", "spreadsheet", "sheets"]


# ----------------------------------------------------------------------------
# Per-layer content correctness
# ----------------------------------------------------------------------------

class TestDataLayer:
    def test_minimal_data_array(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert snap["sheets"][0].get("data") == [
            ["Name", "Value"],
            ["Alice", 42],
        ]

    def test_dates_become_iso_strings(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        # Day 45458 from the 1899-12-30 epoch is 2024-06-15.
        data = snap["sheets"][0].get("data") or []
        assert data[1][1] == "2024-06-15"

    def test_formulas_appear_as_bare_strings(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        data = snap["sheets"][0].get("data") or []
        assert data[1][2] == "=42*2"

    def test_trailing_nulls_trimmed_per_row(self):
        # A row with content at A and C but nothing at B, D, E should produce
        # `[1, null, 3]` — the trailing nulls beyond C are trimmed.
        data: dict[str, Any] = {
            "spreadsheetId": "T",
            "properties": {
                "title": "T", "locale": "en_US", "timeZone": "UTC",
                "defaultFormat": {"textFormat": _arial_10()},
            },
            "sheets": [{
                "properties": {
                    "sheetId": 0, "title": "S", "index": 0,
                    "gridProperties": {"rowCount": 100, "columnCount": 5},
                },
                "data": [{
                    "rowData": [
                        {"values": [
                            {"userEnteredValue": {"numberValue": 1}},
                            {},
                            {"userEnteredValue": {"numberValue": 3}},
                            {},
                            {},
                        ]},
                        # Row 1 establishes a wider data extent so cols D, E
                        # are part of `max_col_seen` — the trim is what removes
                        # them from row 0.
                        {"values": [
                            {"userEnteredValue": {"numberValue": 10}},
                            {"userEnteredValue": {"numberValue": 20}},
                            {"userEnteredValue": {"numberValue": 30}},
                            {"userEnteredValue": {"numberValue": 40}},
                            {"userEnteredValue": {"numberValue": 50}},
                        ]},
                    ],
                    "rowMetadata": [{}, {}], "columnMetadata": [{}] * 5,
                }],
            }],
        }
        snap = _make_spreadsheet(data).snapshot()
        assert snap["sheets"][0].get("data") == [
            [1, None, 3],
            [10, 20, 30, 40, 50],
        ]

    def test_trailing_empty_rows_trimmed(self):
        # Rows that only contributed formatting (no value) become [] after
        # the trailing-null trim. If they sit at the end of `data`, drop them.
        bold = {"textFormat": {**_arial_10(), "bold": True}}
        data: dict[str, Any] = {
            "spreadsheetId": "T",
            "properties": {
                "title": "T", "locale": "en_US", "timeZone": "UTC",
                "defaultFormat": {"textFormat": _arial_10()},
            },
            "sheets": [{
                "properties": {
                    "sheetId": 0, "title": "S", "index": 0,
                    "gridProperties": {"rowCount": 100, "columnCount": 5},
                },
                "data": [{
                    "rowData": [
                        {"values": [
                            {"userEnteredValue": {"stringValue": "first"}},
                        ]},
                        # An empty middle row stays — preserves visual table shape.
                        {"values": [{}]},
                        {"values": [
                            {"userEnteredValue": {"stringValue": "third"}},
                        ]},
                        # Trailing rows: format-only (no value). After the
                        # per-row null trim each becomes [], and being at the
                        # end of the array, they should be dropped entirely.
                        {"values": [{"effectiveFormat": bold}]},
                        {"values": [{"effectiveFormat": bold}]},
                    ],
                    "rowMetadata": [{}, {}, {}, {}, {}],
                    "columnMetadata": [{}],
                }],
            }],
        }
        snap = _make_spreadsheet(data).snapshot()
        assert snap["sheets"][0].get("data") == [
            ["first"],
            [],
            ["third"],
        ]


class TestFormulasMarker:
    def test_formulas_layer_lists_formula_cells(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        sheet = snap["sheets"][0]
        # The single formula in the rich fixture is at C2.
        assert sheet.get("formulas") == ["C2"]

    def test_no_formulas_layer_when_none(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert "formulas" not in snap["sheets"][0]


class TestMerges:
    def test_merge_range(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        assert snap["sheets"][0].get("merges") == ["A1:C1"]

    def test_no_merges_when_none(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert "merges" not in snap["sheets"][0]


class TestFormatsLayer:
    def test_full_row_compaction_for_header(
        self, minimal_spreadsheet: Spreadsheet
    ):
        # Both cells in row 0 are bold → should compact to "1:1".
        snap = minimal_spreadsheet.snapshot()
        formats = snap["sheets"][0].get("formats") or []
        assert any(
            e["range"] == "1:1" and e["fmt"].get("bold") is True
            for e in formats
        )

    def test_number_format_carried_through(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        formats = snap["sheets"][0].get("formats") or []
        # The date column should carry the number_format.
        date_entries = [
            e for e in formats
            if "number_format" in e["fmt"]
            and e["fmt"]["number_format"].startswith("DATE")
        ]
        assert date_entries


class TestBordersLayer:
    def test_bottom_border(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        borders = snap["sheets"][0].get("borders", {})
        # C3 has a bottom border → encoded as horizontal at C3.
        h = borders.get("horizontal") or []
        assert h == [["C3", {"style": "SOLID", "width": 1}]]

    def test_no_borders_when_none(self, minimal_spreadsheet: Spreadsheet):
        snap = minimal_spreadsheet.snapshot()
        assert "borders" not in snap["sheets"][0]


class TestDimensions:
    def test_custom_row_height(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        rows = snap["sheets"][0].get("rows", {})
        assert rows.get("0") == {"height": 32}

    def test_custom_col_width(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        cols = snap["sheets"][0].get("columns", {})
        assert cols.get("A") == {"width": 180}


class TestSideChannels:
    def test_notes(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        assert snap["sheets"][0].get("notes") == {"C2": "Computed"}

    def test_hyperlinks(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()
        assert snap["sheets"][0].get("hyperlinks") == {"A2": "#gid=999"}


class TestComputed:
    def test_default_omits_computed(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot()  # include_computed=False
        assert "computed" not in snap["sheets"][0]

    def test_include_computed_populates(self, rich_spreadsheet: Spreadsheet):
        snap = rich_spreadsheet.snapshot(include_computed=True)
        computed = snap["sheets"][0].get("computed")
        assert computed == {"C2": 84}


# ----------------------------------------------------------------------------
# Layered independence — changing one layer doesn't disturb others
# ----------------------------------------------------------------------------

class TestLayerIndependence:
    def test_value_change_diffs_only_data(self):
        # Two spreadsheets identical except for one cell value.
        d1 = _minimal_data()
        d2 = copy.deepcopy(d1)
        _cell(d2, 0, 1, 1)["userEnteredValue"] = {"numberValue": 999}

        snap1 = _make_spreadsheet(d1).snapshot()
        snap2 = _make_spreadsheet(d2).snapshot()

        sheet1 = snap1["sheets"][0]
        sheet2 = snap2["sheets"][0]
        # Data differs.
        assert sheet1.get("data") != sheet2.get("data")
        # Every other layer is unchanged.
        for layer in (
            "merges", "formulas", "formats", "borders", "rows", "columns",
            "notes", "hyperlinks", "metadata",
        ):
            assert sheet1.get(layer) == sheet2.get(layer)

    def test_format_change_diffs_only_formats(self):
        # Two spreadsheets identical except one cell adds italic.
        d1 = _minimal_data()
        d2 = copy.deepcopy(d1)
        _cell(d2, 0, 1, 0)["effectiveFormat"] = {
            "textFormat": {**_arial_10(), "italic": True}
        }

        snap1 = _make_spreadsheet(d1).snapshot()
        snap2 = _make_spreadsheet(d2).snapshot()

        sheet1 = snap1["sheets"][0]
        sheet2 = snap2["sheets"][0]
        # Formats differ.
        assert sheet1.get("formats") != sheet2.get("formats")
        # Data, merges, formulas, borders all unchanged.
        for layer in ("data", "merges", "formulas", "borders"):
            assert sheet1.get(layer) == sheet2.get(layer)


# ----------------------------------------------------------------------------
# save_snapshot writing contract
# ----------------------------------------------------------------------------

class TestSaveSnapshot:
    def test_output_is_valid_json(self, rich_spreadsheet: Spreadsheet):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as f:
            path = f.name
        try:
            rich_spreadsheet.save_snapshot(path)
            parsed = json.loads(Path(path).read_text(encoding="utf-8"))
            assert parsed["schema_version"] == SCHEMA_VERSION
            assert parsed["sheets"][0]["title"] == "Summary"
        finally:
            Path(path).unlink()

    def test_data_rows_are_single_lines(self, rich_spreadsheet: Spreadsheet):
        # The custom emitter keeps each data row on one line for diff cleanliness.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as f:
            path = f.name
        try:
            rich_spreadsheet.save_snapshot(path)
            text = Path(path).read_text(encoding="utf-8")
            # Find the "data" section. Each row should occupy exactly one line.
            data_lines = [
                line for line in text.split("\n")
                if line.strip().startswith("[") and "," in line
                and "data" not in line
            ]
            # At least one data row line; each must start with [ and end with ] or ],
            for line in data_lines:
                stripped = line.strip()
                assert stripped.startswith("[")
                assert stripped.endswith(("]", "],"))
        finally:
            Path(path).unlink()

    def test_format_entries_are_single_lines(self, rich_spreadsheet: Spreadsheet):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as f:
            path = f.name
        try:
            rich_spreadsheet.save_snapshot(path)
            text = Path(path).read_text(encoding="utf-8")
            # Each format entry contains both "range" and "fmt" on a single line.
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped.startswith('{"range"'):
                    assert '"fmt"' in stripped
        finally:
            Path(path).unlink()
