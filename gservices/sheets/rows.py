from __future__ import annotations
from typing import TYPE_CHECKING, Any, Iterator, Callable

from gservices.sheets.row import Row
from gservices.sheets.utils import array_move

if TYPE_CHECKING:
    from gservices.sheets.sheet import Sheet


class Rows:
    """
    The collection of rows within a Sheet.

    This class allows container-like access to the `Row`s of the Sheet, with features
    like:
      - lazy instantiation of `Row` objects;
      - length and random access;
      - iteration, allowing mutation while iterating
    """

    def __init__(self, sheet: Sheet):
        self._sheet = sheet
        self._nrows: int | None = None
        self._rows: list[Row | None] | None = None
        self._iter_index: int | None = None

    def __len__(self) -> int:
        """
        The number of rows in the sheet with actual data.
        """
        if self._nrows is None:
            if self._sheet._cell_data is not None:
                self._nrows = len(self._sheet._cell_data.get("rowData", []))
            else:
                # This will cause the data to load if it wasn't loaded before
                self._nrows = len(self._sheet.values)
        return self._nrows

    @property
    def limit(self) -> int:
        """
        The maximum number of rows that can be written into the sheet.
        """
        return self._sheet.max_row_count

    @limit.setter
    def limit(self, value: int) -> None:
        self._sheet.max_row_count = value

    def insert(self, before: int | None = None, after: int | None = None) -> Row:
        """
        Inserts a new row either [before] or [after] the specified index, and returns
        the new row just created. If both [before] and [after] are omitted then the
        new row is inserted at the bottom of the sheet.
        """
        if before is not None:
            assert after is None
            assert 0 <= before <= len(self)
            new_index = before
        elif after is not None:
            assert 0 <= after < len(self)
            new_index = after + 1
        else:
            new_index = len(self)
        self._sheet._spreadsheet._add_request({
            "insertDimension": {
                "range": {
                    "sheetId": self._sheet.id,
                    "dimension": "ROWS",
                    "startIndex": new_index,
                    "endIndex": new_index + 1,
                },
                "inheritFromBefore": False,
            }
        })
        self._sheet._handle_row_inserted(new_index)
        self._handle_row_inserted(new_index)
        return self[new_index]

    def sort(self, key_fn: Callable[[Row], Any], skip_rows: int = 0):
        """
        Sorts all rows (except the first [skip_rows]) according to a sort criteria
        expressed by the [key_fn]
        """
        sequence: list[tuple[Any, Row]] = []
        for i in range(skip_rows, len(self)):
            row = self[i]
            key = key_fn(row)
            sequence.append((key, row))
        sequence.sort()
        for i, entry in enumerate(sequence):
            row = entry[1]
            row.move(index=(i + skip_rows))

    def __getitem__(self, index: int) -> Row:
        """
        Returns a Row at the given [index]. The index must be non-negative.
        """
        assert index >= 0
        if self._rows is None:
            rows: list[Row | None] = [None] * len(self)
            self._rows = rows
        if index >= len(self._rows):
            count = index - len(self._rows) + 1
            self._rows += [None] * count
        row = self._rows[index]
        if row is None:
            row = Row(index, self._sheet)
            self._rows[index] = row
        return row

    def __iter__(self) -> Iterator[Row]:
        """
        Iterates through the rows of the sheet. While iterating, you can remove rows
        from the sheet, or add new ones. If a new row is added at an index that was
        already iterated over, then that row will not be returned; if a row is added
        at an index that has not been visited yet, then that row will be included
        in subsequent iteration.
        """
        self._iter_index = 0
        while self._iter_index < len(self):
            yield self[self._iter_index]
            self._iter_index += 1
        self._iter_index = None

    def _handle_row_removed(self, removed_index: int) -> None:
        if self._nrows is not None:
            self._nrows -= 1
        if self._iter_index is not None:
            if removed_index <= self._iter_index:
                self._iter_index -= 1
        if self._rows is not None:
            del self._rows[removed_index]
            for row in self._rows:
                if row is not None and row.index > removed_index:
                    row._index -= 1

    def _handle_row_inserted(self, inserted_index: int) -> None:
        if self._nrows is not None:
            self._nrows += 1
        if self._iter_index is not None:
            if inserted_index <= self._iter_index:
                self._iter_index += 1
        if self._rows is not None:
            self._rows.insert(inserted_index, None)
            for row in self._rows:
                if row is not None and row.index >= inserted_index:
                    row._index += 1

    def _handle_row_moved(self, old_index: int, new_index: int) -> None:
        if self._iter_index is not None:
            if old_index <= self._iter_index < new_index:
                self._iter_index -= 1
            if new_index <= self._iter_index < old_index:
                self._iter_index += 1
        if self._rows is not None:
            array_move(self._rows, old_index, new_index)
            for i, row in enumerate(self._rows):
                if row is not None:
                    row._index = i
