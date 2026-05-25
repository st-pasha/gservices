from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Literal, override

from gservices.sheets.dimension import Dimension

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Row(Dimension):
    """A single row within a Sheet. Indexed from 0."""

    _DIMENSION: ClassVar[Literal["ROWS", "COLUMNS"]] = "ROWS"
    _METADATA_KEY: ClassVar[Literal["rowMetadata", "columnMetadata"]] = "rowMetadata"

    def __init__(self, index: int, sheet: Sheet):
        super().__init__(index, sheet)
        self._metadata: RowDeveloperMetadata | None = None

    @property
    def height(self) -> int:
        return self._properties.get("pixelSize", 21)

    @height.setter
    def height(self, value: int) -> None:
        if value == self.height:
            return
        self._set_property("pixelSize", value)

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
        if self._index >= len(self._sheet.rows) - 1:
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
        self._sheet = None  # type: ignore[assignment]

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
        # Google Sheets API's `destinationIndex` is pre-move; after the source
        # range is removed and re-inserted, a forward move lands one slot
        # earlier than the requested index.
        expected = new_index - 1 if new_index > old_index else new_index
        assert self.index == expected

    def __len__(self) -> int:
        return self._sheet.column_count

    def __getitem__(self, col: int) -> Cell:
        return self._sheet.cell(self._index, col)

    @override
    def __repr__(self) -> str:
        return f"Row(index={self._index})"


from gservices.sheets.cell import Cell
from gservices.sheets.developer_metadata import RowDeveloperMetadata
from gservices.sheets.sheet import Sheet
