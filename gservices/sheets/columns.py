from collections.abc import Callable, Iterator
from typing import Any


class Columns:
    """
    The collection of columns within a Sheet.

    Mirror of `Rows` for the column axis: container-like access with lazy
    `Column` instantiation, length, iteration with mutation support, and sort.
    """

    def __init__(self, sheet: Sheet):
        self._sheet = sheet
        self._ncols: int | None = None
        self._columns: dict[int, Column] = {}
        self._iter_index: int | None = None

    def __len__(self) -> int:
        """
        The number of columns in the sheet with actual data.
        """
        if self._ncols is None:
            if self._sheet._cell_data is not None:
                row_data = self._sheet._cell_data.get("rowData", [])
                if row_data:
                    self._ncols = len(row_data[0].get("values", []))
                else:
                    self._ncols = 0
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
        column = self._columns.get(index)
        if column is None:
            column = Column(index, self._sheet)
            self._columns[index] = column
        return column

    def __iter__(self) -> Iterator[Column]:
        """
        Iterates through the columns of the sheet. While iterating, you can remove
        columns from the sheet, or add new ones. If a new column is added at an
        index that was already iterated over, then that column will not be returned;
        if a column is added at an index that has not been visited yet, then that
        column will be included in subsequent iteration.
        """
        self._iter_index = 0
        while self._iter_index < len(self):
            yield self[self._iter_index]
            self._iter_index += 1
        self._iter_index = None

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

    def sort(self, key_fn: Callable[[Column], Any], skip_columns: int = 0):
        """
        Sorts all columns (except the first [skip_columns]) according to a sort
        criteria expressed by the [key_fn].
        """
        sequence: list[tuple[Any, Column]] = []
        for i in range(skip_columns, len(self)):
            column = self[i]
            key = key_fn(column)
            sequence.append((key, column))
        sequence.sort()
        for i, entry in enumerate(sequence):
            column = entry[1]
            column.move(index=(i + skip_columns))

    def _handle_column_inserted(self, index: int):
        if self._ncols is not None:
            self._ncols += 1
        if self._iter_index is not None and index <= self._iter_index:
            self._iter_index += 1
        # Shift cached Column objects at or past `index` one slot to the right.
        shifted: list[tuple[int, Column]] = []
        for i in [k for k in self._columns if k >= index]:
            column = self._columns.pop(i)
            column._index = i + 1
            shifted.append((i + 1, column))
        for new_i, column in shifted:
            self._columns[new_i] = column

    def _handle_column_removed(self, removed_index: int):
        if self._ncols is not None:
            self._ncols -= 1
        if self._iter_index is not None and removed_index <= self._iter_index:
            self._iter_index -= 1
        # Drop the cached entry at `removed_index`; shift entries past it down.
        self._columns.pop(removed_index, None)
        shifted: list[tuple[int, Column]] = []
        for i in sorted(k for k in self._columns if k > removed_index):
            column = self._columns.pop(i)
            column._index = i - 1
            shifted.append((i - 1, column))
        for new_i, column in shifted:
            self._columns[new_i] = column

    def _handle_column_moved(self, old_index: int, new_index: int):
        if self._iter_index is not None:
            if old_index <= self._iter_index < new_index:
                self._iter_index -= 1
            if new_index <= self._iter_index < old_index:
                self._iter_index += 1
        # Recompute indices for every cached column whose position changed.
        # Simpler to walk all cached entries than to encode the move's index
        # transformation by hand.
        moving = self._columns.pop(old_index, None)
        if old_index < new_index:
            # The moved column lands at new_index - 1 after the gap closes.
            target = new_index - 1
            for i in sorted(k for k in self._columns if old_index < k <= target):
                column = self._columns.pop(i)
                column._index = i - 1
                self._columns[i - 1] = column
        else:
            target = new_index
            for i in sorted(
                (k for k in self._columns if target <= k < old_index),
                reverse=True,
            ):
                column = self._columns.pop(i)
                column._index = i + 1
                self._columns[i + 1] = column
        if moving is not None:
            moving._index = target
            self._columns[target] = moving


from gservices.sheets.column import Column
from gservices.sheets.sheet import Sheet
