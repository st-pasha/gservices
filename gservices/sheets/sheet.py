from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Sequence, cast
from rich.console import Console

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Sheet:
    """
    A single sheet within a spreadsheet.
    """

    def __init__(self, data: gs.Sheet, spreadsheet: Spreadsheet):
        self._spreadsheet = spreadsheet
        self._properties: gs.SheetProperties = data.get("properties", {})
        self._merges: list[gs.GridRange] = data.get("merges", [])
        self._protected: list[gs.ProtectedRange] = data.get("protectedRanges", [])
        self._metadata = SheetDeveloperMetadata(data.get("developerMetadata", []), self)
        self._rows = Rows(self)
        self._columns = Columns(self)
        self._cell_cache: dict[tuple[int, int], Cell] = {}
        self._cell_values: list[list[str]] | None = None
        self._cell_data: gs.GridData | None = data.get("data", [None])[0]

        # Ignored:
        #   conditionalFormats
        #   filterViews
        #   basicFilter
        #   charts
        #   bandedRanges
        #   rowGroups
        #   columnGroups
        #   slicers

    # ----------------------------------------------------------------------------------
    # Sheet properties
    # ----------------------------------------------------------------------------------

    @property
    def id(self) -> int:
        """
        The ID of the sheet, a non-negative number. Note that the ID of a sheet is
        different from its [index], and will not change when the sheet is moved around.

        The ID can be -1 for a sheet which is detached from any spreadsheet.
        """
        return self._properties.get("sheetId", -1)

    @property
    def url(self) -> str:
        return (
            f"https://docs.google.com/spreadsheets/d/{self._spreadsheet.id}/edit"
            f"#gid={self.id}"
        )

    @property
    def type(self) -> Literal["GRID", "OBJECT", "DATA_SOURCE"]:
        """
        The type of the sheet. This field cannot be changed once set.
        """
        type = self._properties.get("sheetType")
        if type is None or type == "SHEET_TYPE_UNSPECIFIED":
            type = "GRID"
        return type

    @property
    def title(self) -> str:
        """
        The name of the sheet. The name cannot contain single quote characters (`'`).
        """
        assert "title" in self._properties
        return self._properties["title"]

    @property
    def index(self) -> int:
        """
        The 0-based index of the sheet within the spreadsheet.
        """
        assert "index" in self._properties
        return self._properties["index"]

    @property
    def hidden(self) -> bool:
        """
        True if the sheet is hidden in the UI, false if it's visible.
        """
        return self._properties.get("hidden", False)

    @property
    def tab_color(self) -> str | None:
        """
        The color of the tab in the UI.
        """
        color = self._properties.get("tabColorStyle")
        return color_object_to_string(color)

    @property
    def max_row_count(self) -> int:
        """
        The number of rows in the grid. This count returns the number of rows visible
        in the UI, not the number of rows with data.
        """
        return self._grid_properties.get("rowCount", 0)

    @property
    def max_column_count(self) -> int:
        """
        The number of columns in the grid. This count returns the number of columns
        visible in the UI, not the number of columns with data.
        """
        return self._grid_properties.get("columnCount", 0)

    @property
    def frozen_row_count(self) -> int:
        """
        The number of rows at the top of the sheet that are "frozen" (i.e. they remain
        visible when the sheet is scrolled).
        """
        return self._grid_properties.get("frozenRowCount", 0)

    @property
    def frozen_column_count(self) -> int:
        """
        The number of columns at the left of the sheet that are "frozen" (i.e. they
        remain visible when the sheet is scrolled).
        """
        return self._grid_properties.get("frozenColumnCount", 0)

    @property
    def hide_gridlines(self) -> bool:
        """
        True if the grid isn't showing gridlines in the UI.
        """
        return self._grid_properties.get("hideGridlines", False)

    @property
    def metadata(self) -> SheetDeveloperMetadata:
        return self._metadata

    @title.setter
    def title(self, value: str) -> None:
        assert "'" not in value
        if self.title == value:
            return
        self._set_property("title", value)

    @index.setter
    def index(self, value: int) -> None:
        self.move(before=value)

    @hidden.setter
    def hidden(self, value: bool) -> None:
        if value == self.hidden:
            return
        self._set_property("hidden", value)

    @tab_color.setter
    def tab_color(self, value: str | None) -> None:
        if value == self.tab_color:
            return
        obj = color_string_to_object(value) or {}
        self._set_property("tabColorStyle", obj)

    @max_row_count.setter
    def max_row_count(self, value: int) -> None:
        if value == self.max_row_count:
            return
        self._set_property("gridProperties.rowCount", value)

    @max_column_count.setter
    def max_column_count(self, value: int) -> None:
        if value == self.max_column_count:
            return
        self._set_property("gridProperties.columnCount", value)

    @frozen_row_count.setter
    def frozen_row_count(self, value: int) -> None:
        if value == self.frozen_row_count:
            return
        self._set_property("gridProperties.frozenRowCount", value)

    @frozen_column_count.setter
    def frozen_column_count(self, value: int) -> None:
        if value == self.frozen_column_count:
            return
        self._set_property("gridProperties.frozenColumnCount", value)

    @hide_gridlines.setter
    def hide_gridlines(self, value: bool) -> None:
        if value == self.hide_gridlines:
            return
        self._set_property("gridProperties.hideGridlines", value)

    def print(self):
        print = Console().print
        print(f"[bold cyan]Sheet:")
        print(f"  [green]title:[/] [bold white]{self.title}")
        print(f"  [green]id:[/] {self.id}")
        print(f"  [green]index:[/] {self.index}")
        print(f"  [green]type:[/] {self.type}")
        print(f"  [green]hidden:[/] {self.hidden}")
        print(f"  [green]tab_color:[/] {self.tab_color}")
        print(f"  [green]max_row_count:[/] {self.max_row_count}")
        print(f"  [green]max_column_count:[/] {self.max_column_count}")
        print(f"  [green]frozen_row_count:[/] {self.frozen_row_count}")
        print(f"  [green]frozen_column_count:[/] {self.frozen_column_count}")
        print(f"  [green]hide_gridlines:[/] {self.hide_gridlines}")
        if self._cell_data is not None or self._cell_values is not None:
            nrows = len(self.rows)
            ncols = self.column_count
        else:
            nrows = "?"
            ncols = "?"
        print(f"  [green]data:[/] \\[{nrows} x {ncols}]")

    # ----------------------------------------------------------------------------------
    # Mutators
    # ----------------------------------------------------------------------------------

    def move(
        self,
        *,
        before: Sheet | int | None = None,
        after: Sheet | int | None = None,
    ) -> None:
        sheets = self._spreadsheet._sheets
        old_index = self.index
        if before is not None:
            if after is not None:
                raise TypeError("Provide either `before` or `after`, but not both")
            if isinstance(before, Sheet):
                new_index = before.index
            else:
                assert 0 <= before <= len(sheets)
                new_index = before
        elif after is not None:
            if isinstance(after, Sheet):
                new_index = after.index + 1
            else:
                assert 0 <= after < len(sheets)
                new_index = after + 1
        else:
            raise KeyError("Either `before` or `after` argument must be provided")
        if new_index == old_index or new_index == old_index + 1:
            return
        sheets.insert(new_index, self)
        if new_index < old_index:
            old_index += 1
        assert sheets[old_index] is self
        del sheets[old_index]
        for i, sheet in enumerate(sheets):
            sheet._properties["index"] = i
        self._set_property("index", new_index)

    def delete(self) -> None:
        self._spreadsheet._add_request({"deleteSheet": {"sheetId": self.id}})
        sheets = self._spreadsheet._sheets
        assert sheets[self.index] is self
        del sheets[self.index]
        for i, sheet in enumerate(sheets):
            sheet._properties["index"] = i

    # ----------------------------------------------------------------------------------
    # Data access
    # ----------------------------------------------------------------------------------

    @property
    def rows(self) -> Rows:
        return self._rows

    @property
    def columns(self) -> Columns:
        return self._columns

    @property
    def column_count(self) -> int:
        if self._cell_data is not None:
            rows = self._cell_data.get("rowData", [])
            return len(rows[0].get("values", [])) if rows else 0
        else:
            # This will cause the data to load if it wasn't loaded before
            rows = self.values
            return len(rows[0]) if rows else 0

    @property
    def values(self) -> Sequence[Sequence[str]]:
        """
        Returns the values in the spreadsheet, as a 2D table of strings. A cell at
        coordinates (row, column) is found at `values[row][column]`.

        The returned list should be treated as immutable. Use [cell()] accessor if
        you need to modify a value in any cell.
        """
        if self._cell_values is None:
            if self._cell_data is None:
                self._load_values()
            else:
                self._fill_values_from_data()
            assert self._cell_values is not None
        return self._cell_values

    def cell(self, row: int, column: int) -> Cell:
        """
        Returns the cell in the given [row] and [column].

        Cell data will be loaded on the first access. The data returned contains the
        cell value, format, and metadata. If you only need to read cell values, then
        it is more efficient to use [.values].
        """
        cached_cell = self._cell_cache.get((row, column))
        if cached_cell is not None:
            return cached_cell
        if row < 0 or column < 0:
            raise ValueError("The `row` and `column` cannot be negative")
        if self._cell_data is None:
            self._load_data()
            assert self._cell_data is not None
        all_rows = self._cell_data.get("rowData")
        if all_rows is None:
            self._cell_data["rowData"] = all_rows = []
        while row >= len(all_rows):
            all_rows.append({"values": []})
        row_values = all_rows[row].get("values")
        if row_values is None:
            all_rows[row]["values"] = row_values = []
        while column >= len(row_values):
            row_values.append({})
        cell = Cell(row, column, row_values[column], self)
        self._cell_cache[(row, column)] = cell
        return cell

    def merge_cells(self, row0: int, col0: int, row1: int, col1: int) -> None:
        """
        Merge all cells between (row0, col0) and (row1, col1), inclusive.
        """
        if row0 == row1 and col0 == col1:
            return
        self._spreadsheet._add_request({
            "mergeCells": {
                "range": {
                    "sheetId": self.id,
                    "startRowIndex": row0,
                    "endRowIndex": row1 + 1,
                    "startColumnIndex": col0,
                    "endColumnIndex": col1 + 1,
                },
                "mergeType": "MERGE_ALL",
            }
        })

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Sheet('{self.title}', id={self.id})"

    def _load_data(self) -> None:
        if self._cell_data is not None:
            return
        data = (
            self._spreadsheet._service.resource.spreadsheets()
            .get(
                spreadsheetId=self._spreadsheet.id,
                ranges=f"'{self.title}'",
                includeGridData=True,
            )
            .execute()
        )
        assert "sheets" in data and len(data["sheets"]) == 1
        assert "data" in data["sheets"][0] and len(data["sheets"][0]["data"]) == 1
        self._cell_data = data["sheets"][0]["data"][0]

    def _load_values(self) -> None:
        data = (
            self._spreadsheet._service.resource.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet.id, range=f"'{self.title}'")
            .execute()
        )
        assert data.get("majorDimension") == "ROWS"
        values = cast(list[list[str]], data.get("values", []))
        n_cols = max(len(row) for row in values)
        for row in values:
            if len(row) < n_cols:
                row += [""] * (n_cols - len(row))
        self._cell_values = values

    def _fill_values_from_data(self) -> None:
        assert self._cell_data is not None
        assert self._cell_values is None
        self._cell_values = []
        for row in self._cell_data.get("rowData", []):
            self._cell_values.append([
                cell_data.get("formattedValue", "")
                for cell_data in row.get("values", [])
            ])
        n_cols = max(len(row) for row in self._cell_values)
        for row in self._cell_values:
            if len(row) < n_cols:
                row += [""] * (n_cols - len(row))

    @property
    def spreadsheet(self) -> Spreadsheet:
        return self._spreadsheet

    def _set_property(self, property: str, value: Any) -> None:
        update: gs.SheetProperties = {"sheetId": self.id}
        set_dotted_property(self._properties, property, value)
        set_dotted_property(update, property, value)
        self._spreadsheet._add_request(
            {
                "updateSheetProperties": {
                    "properties": update,
                    "fields": property,
                }
            }
        )

    @property
    def _grid_properties(self) -> gs.GridProperties:
        return self._properties.get("gridProperties", {})

    def _handle_row_removed(self, removed_row_index: int) -> None:
        if self._cell_values:
            del self._cell_values[removed_row_index]
        if cell_data := self._cell_data:
            if row_data := cell_data.get("rowData"):
                del row_data[removed_row_index]
            if row_meta := cell_data.get("rowMetadata"):
                del row_meta[removed_row_index]
        if cell_cache := self._cell_cache:
            remove_keys = [key for key in cell_cache if key[0] == removed_row_index]
            for key in remove_keys:
                del cell_cache[key]

    def _handle_row_inserted(self, inserted_index: int) -> None:
        if self._cell_values is not None:
            self._cell_values.insert(inserted_index, [""] * self.column_count)
        if self._cell_data is not None:
            self._cell_data.get("rowData", []).insert(inserted_index, {"values": []})
            self._cell_data.get("rowMetadata", []).insert(inserted_index, {})
        update_keys = [key for key in self._cell_cache if key[0] >= inserted_index]
        for key in update_keys:
            cell = self._cell_cache.pop(key)
            cell._row += 1
            self._cell_cache[(key[0] + 1, key[1])] = cell

    def _handle_row_moved(self, old_index: int, new_index: int) -> None:
        if self._cell_values is not None:
            array_move(self._cell_values, old_index, new_index)
        if self._cell_data is not None:
            row_data = self._cell_data.get("rowData")
            row_meta = self._cell_data.get("rowMetadata", [])
            if row_data:
                array_move(row_data, old_index, new_index)
            if row_meta:
                array_move(row_meta, old_index, new_index)
        self._cell_cache.clear()

    def _handle_cell_value_changed(self, irow: int, icol: int, value: str) -> None:
        rows = self._cell_values
        if rows is not None:
            ncols = len(rows[0]) if rows else 0
            while irow >= len(rows):
                rows.append([""] * ncols)
            if icol >= ncols:
                extra_cols = [""] * (icol - ncols + 1)
                for row in rows:
                    row += extra_cols
            rows[irow][icol] = value

    def _handle_column_inserted(self, index: int):
        if (data := self._cell_data) is not None:
            if meta := data.get("columnMetadata"):
                meta.insert(index, {"pixelSize": 100})
            if rows := data.get("rowData"):
                for row in rows:
                    if values := row.get("values"):
                        values.insert(index, {})
        if (data := self._cell_values) is not None:
            for row in data:
                row.insert(index, "")
        update_keys = [key for key in self._cell_cache if key[1] >= index]
        for key in update_keys:
            cell = self._cell_cache.pop(key)
            cell._column += 1
            self._cell_cache[(key[0], key[1] + 1)] = cell

    def _check_integrity(self) -> None:
        nrows = None
        ncols = None
        if (data := self._cell_data) is not None:
            assert data.get("startColumn", 0) == 0
            assert data.get("startRow", 0) == 0
            rows = data.get("rowData", [])
            nrows = len(rows)
            ncols = len(rows[0].get("values", [])) if rows else 0
            for row in rows:
                assert len(row.get("values", [])) <= ncols
            assert len(data.get("rowMetadata", [])) == nrows
            assert len(data.get("columnMetadata", [])) == ncols
        if (data := self._cell_values) is not None:
            if nrows is None:
                nrows = len(data)
                ncols = len(data[0]) if data else 0
            assert len(data) == nrows
            for row in data:
                assert len(row) == ncols
        if nrows is not None:
            assert ncols is not None
            assert self.max_row_count >= nrows
            assert self.max_column_count >= ncols
        assert self.frozen_row_count <= self.max_row_count
        assert self.frozen_column_count <= self.max_column_count


from gservices.sheets.cell import Cell
from gservices.sheets.columns import Columns
from gservices.sheets.developer_metadata import SheetDeveloperMetadata
from gservices.sheets.rows import Rows
from gservices.sheets.spreadsheet import Spreadsheet
from gservices.sheets.utils import (
    array_move,
    color_object_to_string,
    color_string_to_object,
    set_dotted_property,
)
