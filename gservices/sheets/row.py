from __future__ import annotations
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Row:
    def __init__(self, index: int, sheet: Sheet):
        assert index >= 0
        self._index = index
        self._sheet: Sheet = sheet
        self._metadata: RowDeveloperMetadata | None = None

    @property
    def index(self) -> int:
        return self._index

    @property
    def height(self) -> int:
        return self._properties.get("pixelSize", 21)

    @height.setter
    def height(self, value: int) -> None:
        if value == self.height:
            return
        self._set_property("pixelSize", value)

    @property
    def hidden(self) -> bool:
        return self._properties.get("hiddenByUser", False)

    @hidden.setter
    def hidden(self, value: bool) -> None:
        if value == self.hidden:
            return
        self._set_property("hiddenByUser", value)

    @property
    def values(self) -> Sequence[str]:
        return self._sheet.values[self._index]

    @property
    def metadata(self) -> RowDeveloperMetadata:
        if self._metadata is None:
            data = self._properties.get("developerMetadata", [])
            self._metadata = RowDeveloperMetadata(data, self)
        return self._metadata

    @property
    def previous_row(self) -> Row | None:
        if self._index == 0:
            return None
        return self._sheet.rows[self._index - 1]

    @property
    def next_row(self) -> Row | None:
        if self._index == len(self) - 1:
            return None
        return self._sheet.rows[self._index + 1]

    def remove(self) -> None:
        """
        Deletes the row and all its data from the sheet. All remaining rows will be
        moved up. The Row object will become unusable after this call.
        """
        sheet = self._sheet
        index = self._index
        request: gs.DeleteRangeRequest = {
            "shiftDimension": "ROWS",
            "range": {
                "sheetId": sheet.id,
                "startRowIndex": index,
                "endRowIndex": index + 1,
            },
        }
        sheet._spreadsheet._add_request({"deleteRange": request})
        sheet._handle_row_removed(index)
        sheet.rows._handle_row_removed(index)
        self._index = -1
        self._sheet = None  # type: ignore

    def move(
        self,
        *,
        before: Row | None = None,
        after: Row | None = None,
        index: int | None = None,
    ) -> None:
        """
        Moves the current row to be positioned either [before] or [after] the
        specified row, or to the specific row [index].
        """
        sheet = self._sheet
        old_index = self._index
        if index is not None:
            new_index = index
        elif before is not None:
            new_index = before.index
        elif after is not None:
            new_index = after.index + 1
        else:
            new_index = len(sheet.rows)
        if new_index == old_index or new_index == old_index + 1:
            return
        request: gs.MoveDimensionRequest = {
            "source": {
                "sheetId": sheet.id,
                "dimension": "ROWS",
                "startIndex": old_index,
                "endIndex": old_index + 1,
            },
            "destinationIndex": new_index,
        }
        sheet._spreadsheet._add_request({"moveDimension": request})
        sheet._handle_row_moved(old_index, new_index)
        sheet.rows._handle_row_moved(old_index, new_index)
        assert self.index == new_index

    def __len__(self) -> int:
        return self._sheet.column_count

    def __getitem__(self, col: int) -> Cell:
        return self._sheet.cell(self._index, col)

    @property
    def _properties(self) -> gs.DimensionProperties:
        self._sheet._load_data()
        grid_data = self._sheet._cell_data
        assert grid_data is not None
        if row_list := grid_data.get("rowMetadata"):
            if self._index < len(row_list):
                return row_list[self._index]
        return {}

    def _set_property(self, property: str, value: Any) -> None:
        update_properties: gs.DimensionProperties = {}
        set_dotted_property(self._properties, property, value)
        set_dotted_property(update_properties, property, value)
        self._sheet._spreadsheet._add_request({
            "updateDimensionProperties": {
                "properties": update_properties,
                "range": {
                    "sheetId": self._sheet.id,
                    "dimension": "ROWS",
                    "startIndex": self._index,
                    "endIndex": self._index + 1,
                },
                "fields": property,
            }
        })


from gservices.sheets.cell import Cell
from gservices.sheets.developer_metadata import RowDeveloperMetadata
from gservices.sheets.sheet import Sheet
from gservices.sheets.utils import set_dotted_property
