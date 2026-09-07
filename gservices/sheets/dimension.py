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
        self._sheet_ref: Sheet = sheet
        self._removed = False

    @property
    def _sheet(self) -> Sheet:
        if self._removed:
            raise RuntimeError(
                f"This {type(self).__name__} has been removed from the sheet"
            )
        return self._sheet_ref

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
        """
        This dimension's entry in the grid's `rowMetadata`/`columnMetadata`.

        **Materialised, not returned by value.** The API omits the list
        entirely for an axis whose dimensions carry no properties, and omits
        trailing entries for the ones that do not — the ordinary shape for a
        plain row. This used to answer `{}` for those, a throwaway that was not
        part of the model: anything written through it — `hidden`, a
        `pixelSize`, a developer-metadata entry — was queued for the server and
        then vanished locally, so a snapshot taken after the write did not
        contain what had just been written. Padding the list up to the index
        costs the snapshot nothing, since an entry holding no properties
        serialises to nothing.
        """
        self._sheet._load_data()
        grid_data = self._sheet._cell_data
        assert grid_data is not None
        # TypedDict.setdefault with a dynamic key (the class constant) doesn't
        # narrow cleanly — cast and treat it as a plain dict for this lookup.
        meta_list = cast(
            list["gs.DimensionProperties"],
            cast(dict[str, Any], grid_data).setdefault(self._METADATA_KEY, []),
        )
        while len(meta_list) <= self._index:
            meta_list.append({})
        return meta_list[self._index]

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


