from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, override


if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class DeveloperMetadata:
    def __init__(self, data: list[gs.DeveloperMetadata], spreadsheet: Spreadsheet):
        self._data = data
        self._spreadsheet = spreadsheet

    def __len__(self):
        return len(self._data)

    def __getitem__(self, index: int) -> MetadataItem:
        d = self._data[index]
        return MetadataItem(
            id=d.get("metadataId", -1),
            key=d.get("metadataKey", ""),
            value=d.get("metadataValue", ""),
        )

    def __iter__(self) -> Iterator[MetadataItem]:
        for i in range(len(self)):
            yield self[i]

    def __delitem__(self, index: int):
        id = self._data[index].get("metadataId", -1)
        if id >= 0:
            lookup: gs.DeveloperMetadataLookup = {"metadataId": id}
        else:
            lookup = {
                "locationMatchingStrategy": "EXACT_LOCATION",
                "metadataLocation": self._get_location(),
                "metadataKey": self._data[index].get("metadataKey", ""),
            }
        self._spreadsheet._add_request({
            "deleteDeveloperMetadata": {
                "dataFilter": {"developerMetadataLookup": lookup}
            }
        })

    def add(self, key: str, value: str, public: bool = False):
        metadata_item: gs.DeveloperMetadata = {
            "metadataKey": key,
            "metadataValue": value,
            "location": self._get_location(),
            "visibility": "DOCUMENT" if public else "PROJECT",
        }
        self._data.append(metadata_item)
        self._spreadsheet._add_request(
            {"createDeveloperMetadata": {"developerMetadata": metadata_item}},
            callback=self._add_callback,
        )

    def _add_callback(self, response: gs.Response) -> None:
        assert "createDeveloperMetadata" in response, f"Unexpected response: {response}"
        if datum := response["createDeveloperMetadata"].get("developerMetadata", None):
            if key := datum.get("metadataKey", None):
                for i, item in enumerate(self._data):
                    if item.get("metadataKey") == key:
                        self._data[i] = datum
                        break

    def _get_location(self) -> gs.DeveloperMetadataLocation:
        raise NotImplementedError()


class SpreadsheetDeveloperMetadata(DeveloperMetadata):
    @override
    def _get_location(self) -> gs.DeveloperMetadataLocation:
        return {"spreadsheet": True}


class SheetDeveloperMetadata(DeveloperMetadata):
    def __init__(self, data: list[gs.DeveloperMetadata], sheet: Sheet):
        super().__init__(data, sheet._spreadsheet)
        self._sheet = sheet

    @override
    def _get_location(self) -> gs.DeveloperMetadataLocation:
        return {"sheetId": self._sheet.id}


class RowDeveloperMetadata(DeveloperMetadata):
    def __init__(self, data: list[gs.DeveloperMetadata], row: Row):
        super().__init__(data, row._sheet._spreadsheet)
        self._row = row

    @override
    def _get_location(self) -> gs.DeveloperMetadataLocation:
        return {
            "dimensionRange": {
                "sheetId": self._row._sheet.id,
                "dimension": "ROWS",
                "startIndex": self._row.index,
                "endIndex": self._row.index + 1,
            },
        }


class ColumnDeveloperMetadata(DeveloperMetadata):
    def __init__(self, data: list[gs.DeveloperMetadata], column: Column):
        super().__init__(data, column._sheet._spreadsheet)
        self._column = column

    @override
    def _get_location(self) -> gs.DeveloperMetadataLocation:
        return {
            "dimensionRange": {
                "sheetId": self._column._sheet.id,
                "dimension": "COLUMNS",
                "startIndex": self._column.index,
                "endIndex": self._column.index + 1,
            },
        }

    def __repr__(self) -> str:
        if len(self) <= 5:
            return f"{self.__class__.__name__}({list(self)})"
        else:
            parts = [str(self[i]) for i in range(4)]
            return f"{self.__class__.__name__}([{", ".join(parts)}, ...])"


@dataclass
class MetadataItem:
    id: int
    key: str
    value: str


from gservices.sheets.spreadsheet import Spreadsheet
from gservices.sheets.sheet import Sheet
from gservices.sheets.row import Row
from gservices.sheets.column import Column
