from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from gservices.sheets.utils import set_dotted_property

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

    from gservices.sheets.sheet import Sheet


class Dimension:
    """Shared base for `Row` and `Column`.

    Subclasses declare two class-level constants:
      - `_DIMENSION` — `"ROWS"` or `"COLUMNS"`, used in batchUpdate requests.
      - `_METADATA_KEY` — `"rowMetadata"` or `"columnMetadata"`, the key
        under `_cell_data` where this axis's `DimensionProperties` live.
    """

    _DIMENSION: ClassVar[Literal["ROWS", "COLUMNS"]]
    _METADATA_KEY: ClassVar[Literal["rowMetadata", "columnMetadata"]]

    def __init__(self, index: int, sheet: Sheet):
        assert index >= 0
        self._index = index
        self._sheet: Sheet = sheet

    @property
    def index(self) -> int:
        return self._index

    @property
    def hidden(self) -> bool:
        return self._properties.get("hiddenByUser", False)

    @hidden.setter
    def hidden(self, value: bool) -> None:
        if value == self.hidden:
            return
        self._set_property("hiddenByUser", value)

    @property
    def _properties(self) -> gs.DimensionProperties:
        self._sheet._load_data()
        grid_data = self._sheet._cell_data
        assert grid_data is not None
        # TypedDict.get with a dynamic key (the class constant) doesn't narrow
        # cleanly — cast and read it as a plain dict for this one lookup.
        meta_list = cast(dict[str, Any], grid_data).get(self._METADATA_KEY)
        if meta_list and self._index < len(meta_list):
            return meta_list[self._index]
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
                    "dimension": self._DIMENSION,
                    "startIndex": self._index,
                    "endIndex": self._index + 1,
                },
                "fields": property,
            }
        })


