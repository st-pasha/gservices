from __future__ import annotations
import time
from typing import TYPE_CHECKING, Any, Sequence
from rich.console import Console

from gservices.drive.spreadsheet_file import SpreadsheetFile
from gservices.sheets.cell_format import CellFormat
from gservices.sheets.sheet import Sheet
from gservices.sheets.utils import (
    color_object_to_string,
    merge_requests,
    set_dotted_property,
)

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]
    from gservices.sheets.sheets_service import SheetsService


class Spreadsheet:
    """
    [Spreadsheet] represents a single Google Sheets document, stored on Google Drive.

    In order to open a Spreadsheet, you need the ID of the underlying document, and
    then call [SheetsService.open()]:

        spreadsheet = google_service.Sheets.open(spreadsheet_id)

    Spreadsheet properties and cell values can be modified through this object. All such
    changes will be queued until you run [save()], at which point they will be uploaded
    to the server in one or more batches.
    """

    BATCH_SIZE = 500

    def __init__(self, data: gs.Spreadsheet, service: SheetsService):
        self._service = service
        self._id: str = data.get("spreadsheetId", "")
        self._url: str = data.get("spreadsheetUrl", "")
        self._properties: gs.SpreadsheetProperties = data.get("properties", {})
        self._sheets = [
            Sheet(data=item, spreadsheet=self) for item in data.get("sheets", [])
        ]
        # The list of all updates that are scheduled to be applied to the spreadsheet
        # on the next `save()`.
        self._pending_updates: list[gs.Request] = []

    def save(self) -> None:
        """
        Saves any pending changes to the spreadsheet file stored in Google Cloud.
        """
        if not self._pending_updates:
            return
        i0 = 0
        while i0 < len(self._pending_updates):
            updates = self._pending_updates[i0 : i0 + Spreadsheet.BATCH_SIZE]
            Console().print(updates)
            (
                self._service.resource.spreadsheets()
                .batchUpdate(
                    spreadsheetId=self._id,
                    body={
                        "requests": updates,
                        "includeSpreadsheetInResponse": False,
                    },
                )
                .execute()
            )
            i0 += Spreadsheet.BATCH_SIZE
        self._pending_updates = []
        time.sleep(1)

    # ----------------------------------------------------------------------------------
    # Basic properties
    # ----------------------------------------------------------------------------------

    @property
    def id(self) -> str:
        """
        The ID of the spreadsheet. This is the same as the file ID in Google Drive.
        """
        return self._id

    @property
    def url(self) -> str:
        """
        The url of the spreadsheet, derived from its ID. This field is read-only.
        """
        return self._url

    @property
    def title(self) -> str:
        """
        The title of the spreadsheet. This is the same as the spreadsheet file name in
        Google Drive.
        """
        return self._properties.get("title", "")

    @title.setter
    def title(self, value: str) -> None:
        self._set_property("title", value)

    @property
    def locale(self) -> str:
        """
        The locale of the spreadsheet in one of the following formats:
            - an ISO 639-1 language code such as en
            - an ISO 639-2 language code such as fil, if no 639-1 code exists
            - a combination of the ISO language code and country code, such as en_US
        """
        return self._properties.get("locale", "")

    @locale.setter
    def locale(self, value: str) -> None:
        self._set_property("locale", value)

    @property
    def time_zone(self) -> str:
        """
        The time zone of the spreadsheet, in CLDR format such as America/New_York.
        If the time zone isn't recognized, this may be a custom time zone such as
        GMT-07:00.
        """
        return self._properties.get("timeZone", "")

    @time_zone.setter
    def time_zone(self, value: str) -> None:
        self._set_property("timeZone", value)

    @property
    def theme(self) -> gs.SpreadsheetTheme:
        """
        Theme applied to the spreadsheet.

        The theme contains the main font family, as well as 9 primary colors: TEXT,
        BACKGROUND, LINK, and ACCENT1-ACCENT6.
        """
        return self._properties.get("spreadsheetTheme", {})

    @property
    def default_cell_format(self) -> CellFormat:
        """
        The default format for all cells in the spreadsheet. This field is read-only.
        """
        return CellFormat(self._properties.get("defaultFormat", {}), cell=None)

    @property
    def file(self) -> SpreadsheetFile:
        file = self._service._google.Drive.get(id=self.id)
        assert isinstance(file, SpreadsheetFile)
        return file

    def print(self):
        print = Console().print
        print(f"[bold cyan]Spreadsheet:")
        print(f"  [green]title:[/] [bold white]{self.title}")
        print(f"  [green]id:[/] {self.id}")
        print(f"  [green]url:[/] {self.url}")
        print(f"  [green]locale:[/] {self.locale}")
        print(f"  [green]time_zone:[/] {self.time_zone}")
        print(f"  [green]theme:[/]")
        print(f"    [green]font_family:[/] {self.theme.get('primaryFontFamily')}")
        print(f"    [green]colors:[/]")
        for record in self.theme.get("themeColors", []):
            color = color_object_to_string(record.get("color", {}))
            print(f"      [green]{record.get('colorType')}:[/] {color}")
        print(f"  [green]cell_format:")
        self.default_cell_format.print(indent="    ")
        print(f"  [green]sheets:")
        for sheet in self.sheets:
            print(
                f"    [magenta not bold]\\[{sheet.index}][/]: "
                f"[bold white]{sheet.title}[/], id={sheet.id}"
            )

    # ----------------------------------------------------------------------------------
    # Sheets
    # ----------------------------------------------------------------------------------

    @property
    def sheets(self) -> Sequence[Sheet]:
        """
        The list of sheets in the spreadsheet. The list should not be modified by the
        user directly -- instead use [add_sheet()], [delete_sheet()] or [move_sheet()].

        Hidden sheets are included in the list.
        """
        return self._sheets

    @property
    def visible_sheets(self) -> list[Sheet]:
        """
        The list of sheets excluding any hidden sheets.
        """
        return [sheet for sheet in self._sheets if not sheet.hidden]

    def sheet(self, name: str) -> Sheet | None:
        """
        Finds a sheet with the given [name], or returns None if a sheet with such
        name does not exist.
        """
        for sheet in self._sheets:
            if sheet.title == name:
                return sheet
        return None

    def add_sheet(self, name: str) -> Sheet:
        """
        Creates a new sheet with the given [name] and adds it at the end of the
        sheet list.
        """
        max_id = max(sheet.id for sheet in self._sheets)
        properties: gs.SheetProperties = {
            "sheetId": max_id + 1,
            "sheetType": "GRID",
            "title": name,
            "index": len(self._sheets),
        }
        self._add_request({"addSheet": {"properties": properties}})
        sheet = Sheet({"properties": properties}, self)
        self._sheets.append(sheet)
        return sheet

    def delete_sheet(self, sheet: Sheet | str) -> None:
        """
        Deletes the given [sheet] from the spreadsheet.
        """
        if isinstance(sheet, str):
            sheet_obj = self.sheet(sheet)
            if not sheet_obj:
                raise KeyError(f"Sheet `{sheet}` does not exist in the spreadsheet")
            sheet = sheet_obj
        assert sheet._spreadsheet is self
        sheet.delete()

    def move_sheet(
        self,
        sheet: Sheet | str,
        *,
        before: Sheet | str | int | None = None,
        after: Sheet | str | int | None = None,
    ) -> None:
        """
        Moves the [sheet] either [before] or [after] another sheet.
        """
        if isinstance(sheet, str):
            sheet_obj = self.sheet(sheet)
            if sheet_obj is None:
                raise KeyError(f"Unknown sheet name {sheet!r}")
        else:
            sheet_obj = sheet
        if before is not None:
            if isinstance(before, str):
                before_sheet = self.sheet(before)
                if not before_sheet:
                    raise KeyError(f"Unknown `before` sheet {before!r}")
                before = before_sheet.index
            if isinstance(before, Sheet):
                before = before.index
        if after is not None:
            if isinstance(after, str):
                after_sheet = self.sheet(after)
                if not after_sheet:
                    raise KeyError(f"Unknown `after` sheet {after!r}")
                after = after_sheet.index
            if isinstance(after, Sheet):
                after = after.index
        sheet_obj.move(before=before, after=after)

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def __repr__(self) -> str:
        n = len(self._sheets)
        return f"Spreadsheet({self.title!r}, id='{self.id}', #sheets={n})"

    def _set_property(self, property: str, value: Any) -> None:
        update_properties: gs.SpreadsheetProperties = {}
        set_dotted_property(self._properties, property, value)
        set_dotted_property(update_properties, property, value)
        self._add_request({
            "updateSpreadsheetProperties": {
                "properties": update_properties,
                "fields": property,
            }
        })

    def _add_request(
        self,
        request: gs.Request,
    ) -> None:
        if self._pending_updates:
            previous_request = self._pending_updates[-1]
            if merge_requests(previous_request, request):
                return
        self._pending_updates.append(request)
