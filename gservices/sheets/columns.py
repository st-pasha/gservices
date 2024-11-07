from __future__ import annotations
from typing import TYPE_CHECKING

from gservices.sheets.column import Column

if TYPE_CHECKING:
    from gservices.sheets.sheet import Sheet


class Columns:
    def __init__(self, sheet: Sheet):
        self._sheet = sheet
        self._ncols: int | None = None

    def __len__(self) -> int:
        """
        The number of columns in the sheet with actual data.
        """
        if self._ncols is None:
            if self._sheet._cell_data is not None:
                self._ncols = len(self._sheet._cell_data.get("rowData", [])[0])
            else:
                # This will cause the data to load if it wasn't loaded before
                values = self._sheet.values
                self._ncols = len(values[0]) if values else 0
        return self._ncols

    @property
    def limit(self) -> int:
        """
        The maximum number of columns that can be written into the sheet.
        """
        return self._sheet.max_column_count

    @limit.setter
    def limit(self, value: int) -> None:
        self._sheet.max_column_count = value

    def __getitem__(self, index: int) -> Column:
        return Column(index, self._sheet)

    def insert(
        self,
        before: int | Column | None = None,
        after: int | Column | None = None,
    ) -> Column:
        """
        Inserts a new column either [before] or [after] the specified index, and returns
        the new column just created. If both [before] and [after] are omitted then the
        new column is inserted in the rightmost position.
        """
        if before is not None:
            assert after is None
            if isinstance(before, Column):
                new_index = before.index
            else:
                assert 0 <= before <= len(self)
                new_index = before
        elif after is not None:
            if isinstance(after, Column):
                new_index = after.index + 1
            else:
                assert 0 <= after < len(self)
                new_index = after + 1
        else:
            new_index = len(self)
        self._sheet._spreadsheet._add_request({
            "insertDimension": {
                "range": {
                    "sheetId": self._sheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": new_index,
                    "endIndex": new_index + 1,
                },
                "inheritFromBefore": False,
            }
        })
        self._sheet._handle_column_inserted(new_index)
        self._handle_column_inserted(new_index)
        return self[new_index]

    def _handle_column_inserted(self, index: int):
        if self._ncols is not None:
            self._ncols += 1
