from typing import TYPE_CHECKING, ClassVar, Literal, override

from gservices.sheets.dimension import Dimension

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Column(Dimension):
    """A single column within a Sheet. Indexed from 0."""

    _DIMENSION: ClassVar[Literal["ROWS", "COLUMNS"]] = "COLUMNS"
    _METADATA_KEY: ClassVar[Literal["rowMetadata", "columnMetadata"]] = "columnMetadata"

    def __init__(self, index: int, sheet: Sheet):
        super().__init__(index, sheet)
        self._metadata: ColumnDeveloperMetadata | None = None

    @property
    def width(self) -> int:
        return self._properties.get("pixelSize", 100)

    @width.setter
    def width(self, value: int) -> None:
        if value == self.width:
            return
        self._set_property("pixelSize", value)

    @property
    def metadata(self) -> ColumnDeveloperMetadata:
        if self._metadata is None:
            data = self._properties.get("developerMetadata", [])
            self._metadata = ColumnDeveloperMetadata(data, self)
        return self._metadata

    @property
    def previous_column(self) -> Column | None:
        if self._index == 0:
            return None
        return self._sheet.columns[self._index - 1]

    @property
    def next_column(self) -> Column | None:
        if self._index >= len(self._sheet.columns) - 1:
            return None
        return self._sheet.columns[self._index + 1]

    def remove(self) -> None:
        """
        Deletes the column and all its data from the sheet. All remaining columns
        will be moved left. The Column object will become unusable after this call.
        """
        sheet = self._sheet
        index = self._index
        request: gs.DeleteRangeRequest = {
            "shiftDimension": "COLUMNS",
            "range": {
                "sheetId": sheet.id,
                "startColumnIndex": index,
                "endColumnIndex": index + 1,
            },
        }
        sheet._spreadsheet._add_request({"deleteRange": request})
        sheet._handle_column_removed(index)
        sheet.columns._handle_column_removed(index)
        self._index = -1
        self._sheet = None  # type: ignore[assignment]

    def move(
        self,
        *,
        before: Column | None = None,
        after: Column | None = None,
        index: int | None = None,
    ) -> None:
        """
        Moves the current column to be positioned either [before] or [after] the
        specified column, or to the specific column [index].
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
            new_index = len(sheet.columns)
        if new_index == old_index or new_index == old_index + 1:
            return
        request: gs.MoveDimensionRequest = {
            "source": {
                "sheetId": sheet.id,
                "dimension": "COLUMNS",
                "startIndex": old_index,
                "endIndex": old_index + 1,
            },
            "destinationIndex": new_index,
        }
        sheet._spreadsheet._add_request({"moveDimension": request})
        sheet._handle_column_moved(old_index, new_index)
        sheet.columns._handle_column_moved(old_index, new_index)
        # Google Sheets API's `destinationIndex` is pre-move; after the source
        # range is removed and re-inserted, a forward move lands one slot
        # earlier than the requested index.
        expected = new_index - 1 if new_index > old_index else new_index
        assert self.index == expected

    @override
    def __repr__(self) -> str:
        return f"Column(index={self._index})"


from gservices.sheets.developer_metadata import ColumnDeveloperMetadata
from gservices.sheets.sheet import Sheet
