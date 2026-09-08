"""
Tests for `extent="data"` — loading only the populated part of a sheet.

The point of the mode is what it *doesn't* fetch, so these assert on the
requests that were made as much as on the model they produce.
"""

from typing import Any, cast

import pytest

from gservices.sheets.spreadsheet import Spreadsheet
from gservices.sheets.utils import quote_sheet_title

# ----------------------------------------------------------------------------
# A fake Sheets API that answers the three shapes of request this mode makes
# ----------------------------------------------------------------------------

class _Request:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        return self._payload


class _FakeApi:
    """
    Stands in for `SheetsService`, recording what it was asked for.

    Dispatches on the shape of the call rather than on call order, so a test
    that changes how many requests are made still reads the right response.
    """

    def __init__(
        self,
        *,
        values: dict[str, list[list[str]]],
        grid: dict[int, list[dict[str, Any]]],
        dimensions: dict[int, dict[str, Any]],
    ):
        self._values = values
        self._grid = grid
        self._dimensions = dimensions
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # The chain `service.resource.spreadsheets()` and `.values()` all land here.
    @property
    def resource(self) -> _FakeApi:
        return self

    def spreadsheets(self) -> _FakeApi:
        return self

    def values(self) -> _FakeApi:
        return self

    def batchGet(self, **kwargs: Any) -> _Request:  # Google's spelling
        self.calls.append(("values.batchGet", kwargs))
        ranges = cast(list[str], kwargs["ranges"])
        return _Request({
            "valueRanges": [
                {"values": self._values.get(r, [])} for r in ranges
            ]
        })

    def get(self, **kwargs: Any) -> _Request:
        if "range" in kwargs:  # spreadsheets().values().get(...)
            self.calls.append(("values.get", kwargs))
            return _Request({
                "majorDimension": "ROWS",
                "values": self._values.get(cast(str, kwargs["range"]), []),
            })
        fields = cast(str, kwargs.get("fields", ""))
        if "ranges" in kwargs:
            self.calls.append(("get.bounded", kwargs))
            sheets: list[dict[str, Any]] = []
            for rng in cast(list[str], kwargs["ranges"]):
                sheet_id = _sheet_id_for_range(rng)
                sheets.append({
                    "properties": {"sheetId": sheet_id},
                    "data": [{"rowData": self._grid.get(sheet_id, [])}],
                })
            return _Request({"sheets": sheets})
        if "rowMetadata" in fields and "rowData" not in fields:
            self.calls.append(("get.dimensions", kwargs))
            return _Request({
                "sheets": [
                    {"properties": {"sheetId": sid}, "data": [block]}
                    for sid, block in self._dimensions.items()
                ]
            })
        self.calls.append(("get.whole", kwargs))
        return _Request({
            "sheets": [
                {
                    "properties": {"sheetId": sid},
                    "data": [{"rowData": rows, **self._dimensions.get(sid, {})}],
                }
                for sid, rows in self._grid.items()
            ]
        })

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def kwargs_for(self, kind: str) -> dict[str, Any]:
        for seen, kwargs in self.calls:
            if seen == kind:
                return kwargs
        raise AssertionError(f"no {kind} request was made; got {self.kinds()}")


_TITLES = {"Data": 11, "Empty": 22}


def _sheet_id_for_range(rng: str) -> int:
    title = rng.split("!")[0].strip("'").replace("''", "'")
    return _TITLES[title]


def _cell(value: str) -> dict[str, Any]:
    return {"userEnteredValue": {"stringValue": value}}


def _spreadsheet(api: _FakeApi, extent: str = "data") -> Spreadsheet:
    data: dict[str, Any] = {
        "spreadsheetId": "TEST",
        "properties": {"title": "T", "locale": "en_US", "timeZone": "UTC"},
        "sheets": [
            {
                "properties": {
                    "sheetId": 11,
                    "title": "Data",
                    "index": 0,
                    "gridProperties": {"rowCount": 1000, "columnCount": 26},
                },
                "data": [],
            },
            {
                "properties": {
                    "sheetId": 22,
                    "title": "Empty",
                    "index": 1,
                    "gridProperties": {"rowCount": 1000, "columnCount": 26},
                },
                "data": [],
            },
        ],
    }
    return Spreadsheet(cast(Any, data), cast(Any, api), extent=cast(Any, extent))


def _api(**overrides: Any) -> _FakeApi:
    """A workbook whose `Data` tab holds a 2x3 block in a 1000x26 grid."""
    defaults: dict[str, Any] = {
        "values": {
            "'Data'": [["a", "b", "c"], ["d", "e", "f"]],
            "'Empty'": [],
        },
        "grid": {
            11: [
                {"values": [_cell("a"), _cell("b"), _cell("c")]},
                {"values": [_cell("d"), _cell("e"), _cell("f")]},
            ],
        },
        "dimensions": {
            11: {
                # Row 500 is far outside the data extent and carries an id —
                # the case the unbounded metadata request exists for.
                "rowMetadata": [{} for _ in range(500)]
                + [{"developerMetadata": [{"metadataKey": "id", "metadataValue": "x"}]}],
                "columnMetadata": [{"pixelSize": 140}],
            },
        },
    }
    defaults.update(overrides)
    return _FakeApi(**defaults)


# ----------------------------------------------------------------------------
# What gets asked for
# ----------------------------------------------------------------------------

class TestRequests:
    def test_grid_is_bounded_to_the_values_extent(self):
        api = _api()
        ss = _spreadsheet(api)
        ss._load_all_data()
        assert api.kwargs_for("get.bounded")["ranges"] == ["'Data'!A1:C2"]

    def test_a_sheet_with_no_values_is_not_asked_for(self):
        api = _api()
        ss = _spreadsheet(api)
        ss._load_all_data()
        ranges = api.kwargs_for("get.bounded")["ranges"]
        assert not any("Empty" in r for r in ranges)

    def test_dimension_properties_are_asked_for_unbounded(self):
        api = _api()
        ss = _spreadsheet(api)
        ss._load_all_data()
        kwargs = api.kwargs_for("get.dimensions")
        assert "ranges" not in kwargs
        assert "rowData" not in kwargs["fields"]

    def test_grid_extent_makes_one_whole_grid_request(self):
        api = _api()
        ss = _spreadsheet(api, extent="grid")
        ss._load_all_data()
        assert api.kinds() == ["get.whole"]

    def test_computed_values_reach_the_bounded_mask(self):
        api = _api()
        ss = _spreadsheet(api)
        ss._load_all_data(include_computed=True)
        assert "effectiveValue" in api.kwargs_for("get.bounded")["fields"]


# ----------------------------------------------------------------------------
# What the model ends up holding
# ----------------------------------------------------------------------------

class TestLoadedModel:
    def test_cells_come_from_the_bounded_grid(self):
        ss = _spreadsheet(_api())
        ss._load_all_data()
        # `user_entered_value`, not `value`: the batched mask leaves out
        # `effectiveValue`, which is what `value` reads.
        assert ss.sheets[0].cell(1, 2).user_entered_value == "f"

    def test_row_metadata_outside_the_extent_survives(self):
        ss = _spreadsheet(_api())
        snap = ss.snapshot()
        rows = cast(dict[str, Any], snap["sheets"][0].get("rows", {}))
        assert rows["500"]["metadata"] == [
            {"key": "id", "value": "x", "public": False}
        ]

    def test_column_properties_survive(self):
        ss = _spreadsheet(_api())
        snap = ss.snapshot()
        cols = cast(dict[str, Any], snap["sheets"][0].get("columns", {}))
        assert cols["A"]["width"] == 140

    def test_values_come_from_the_extent_call(self):
        api = _api()
        ss = _spreadsheet(api)
        ss._load_all_data()
        assert ss.sheets[0].values == [["a", "b", "c"], ["d", "e", "f"]]
        # No `values.get` on top of the `values.batchGet` already made.
        assert api.kinds().count("values.batchGet") == 1

    def test_an_empty_sheet_loads_and_stays_loaded(self):
        api = _api()
        ss = _spreadsheet(api)
        empty = ss.sheets[1]
        assert len(empty.rows) == 0
        assert empty.values == []
        before = len(api.calls)
        assert len(empty.rows) == 0
        assert len(api.calls) == before

    def test_a_single_sheet_load_is_bounded_too(self):
        api = _api()
        ss = _spreadsheet(api)
        ss.sheets[0].load()
        assert api.kwargs_for("get.bounded")["ranges"] == ["'Data'!A1:C2"]

    def test_declared_grid_size_is_unchanged(self):
        ss = _spreadsheet(_api())
        snap = ss.snapshot()
        assert snap["sheets"][0]["grid"] == {"rows": 1000, "cols": 26}


# ----------------------------------------------------------------------------
# A1 quoting
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("title", "quoted"),
    [
        ("Sheet1", "'Sheet1'"),
        ("02 Thu ", "'02 Thu '"),
        ("A1", "'A1'"),
        ("2024", "'2024'"),
        ("Bob's tab", "'Bob''s tab'"),
    ],
)
def test_quote_sheet_title(title: str, quoted: str):
    assert quote_sheet_title(title) == quoted
