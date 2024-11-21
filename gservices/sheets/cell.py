from __future__ import annotations
from rich.console import Console
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

pprint = Console().print


class Cell:
    def __init__(self, row: int, column: int, data: gs.CellData, sheet: Sheet):
        self._row = row
        self._column = column
        self._sheet = sheet
        self._data = data
        self._format: CellFormat | None = None

    @property
    def row(self) -> int:
        """0-based row number for the cell."""
        return self._row

    @property
    def column(self) -> int:
        """0-based column number for the cell."""
        return self._column

    @property
    def name(self) -> str:
        """
        The address of the cell in Excel notation, such as "B102".
        """
        return coords_to_address(self._row, self._column)

    @property
    def url(self) -> str:
        return f"{self._sheet.url}&range={self.name}"

    @property
    def value(self) -> CellValue:
        return self.effective_value

    @value.setter
    def value(self, new_value: CellValue) -> None:
        if new_value == "":
            new_value = None
        if new_value == self.value or (
            isinstance(new_value, HyperlinkFormula)
            and new_value.url == self.hyperlink
            and new_value.label == self.formatted_value
        ):
            return
        v = python_to_value(new_value)
        vv = str(new_value)
        self._set_property("userEnteredValue", v)
        self._data["effectiveValue"] = v
        self._data["formattedValue"] = vv
        self._sheet._handle_cell_value_changed(self._row, self._column, vv)

    @property
    def user_entered_value(self) -> "CellValue":
        """
        The value the user entered in the cell. e.g., 1234, 'Hello', or `=NOW()`.
        Note: Dates, Times and DateTimes are represented as doubles in serial number
        format.
        """
        return value_to_python(self._data.get("userEnteredValue"))

    @property
    def effective_value(self) -> "CellValue":
        """
        The effective value of the cell. For cells with formulas, this is the
        calculated value. For cells with literals, this is the same as the
        [userEnteredValue]. This field is read-only.
        """
        return value_to_python(self._data.get("effectiveValue"))

    @property
    def formatted_value(self) -> str:
        """
        The formatted value of the cell. This is the value as it's shown to the user.
        This field is read-only.
        """
        return self._data.get("formattedValue", "")

    @property
    def hyperlink(self) -> str | None:
        """
        A hyperlink this cell points to, if any. If the cell contains multiple
        hyperlinks, this field will be empty.

        In order to set a URL on a cell, assign a `hyperlink()` formula to the
        cell's [value].
        """
        return self._data.get("hyperlink")

    @property
    def note(self) -> str:
        """
        A note attached to the cell, if any.
        """
        return self._data.get("note", "")

    @note.setter
    def note(self, value: str):
        if value == self.note:
            return
        self._set_property("note", value)

    @property
    def format(self) -> CellFormat:
        if self._format is None:
            self._format = CellFormat(self._data.get("effectiveFormat", {}), self)
        return self._format

    @format.setter
    def format(self, value: gs.CellFormat | None):
        if cell_formats_equal(value, self._data.get("userEnteredFormat")):
            return
        self._set_property("userEnteredFormat", value)

    def print(self, indent: str = "") -> None:
        if indent:
            i = indent
        else:
            i = "  "
            pprint(f"[bold cyan]Cell:")
        pprint(f"{i}[green]user_entered_value:[/] {self.user_entered_value}")
        pprint(f"{i}[green]effective_value:[/] {self.effective_value}")
        pprint(f"{i}[green]formatted_value:[/] {self.formatted_value}")
        pprint(f"{i}[green]hyperlink:[/] {self.hyperlink}")
        pprint(f"{i}[green]note:[/] {self.note}")
        pprint(f"{i}[green]format:[/]")
        self.format.print(i + "  ")

    def _set_property(self, property: str, value: Any) -> None:
        update_data: gs.CellData = {}
        set_dotted_property(self._data, property, value)
        set_dotted_property(update_data, property, value)
        self._sheet._spreadsheet._add_request({
            "updateCells": {
                "rows": [{"values": [update_data]}],
                "start": {
                    "sheetId": self._sheet.id,
                    "rowIndex": self._row,
                    "columnIndex": self._column,
                },
                "fields": property,
            }
        })


from gservices.sheets.cell_format import CellFormat
from gservices.sheets.cell_value import (
    CellValue,
    HyperlinkFormula,
    python_to_value,
    value_to_python,
)
from gservices.sheets.sheet import Sheet
from gservices.sheets.utils import (
    coords_to_address,
    cell_formats_equal,
    set_dotted_property,
)
