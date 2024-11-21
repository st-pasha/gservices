from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Column:
    def __init__(self, index: int, sheet: Sheet):
        assert index >= 0
        self._index = index
        self._sheet = sheet
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
    def hidden(self) -> bool:
        return self._properties.get("hiddenByUser", False)

    @hidden.setter
    def hidden(self, value: bool) -> None:
        if value == self.hidden:
            return
        self._set_property("hiddenByUser", value)

    @property
    def index(self) -> int:
        return self._index

    @property
    def metadata(self) -> ColumnDeveloperMetadata:
        if self._metadata is None:
            data = self._properties.get("developerMetadata", [])
            self._metadata = ColumnDeveloperMetadata(data, self)
        return self._metadata

    @property
    def _properties(self) -> gs.DimensionProperties:
        self._sheet._load_data()
        grid_data = self._sheet._cell_data
        assert grid_data is not None
        if column_list := grid_data.get("columnMetadata"):
            if self._index < len(column_list):
                return column_list[self._index]
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
                    "dimension": "COLUMNS",
                    "startIndex": self._index,
                    "endIndex": self._index + 1,
                },
                "fields": property,
            }
        })


from gservices.sheets.developer_metadata import ColumnDeveloperMetadata
from gservices.sheets.sheet import Sheet
from gservices.sheets.utils import set_dotted_property
