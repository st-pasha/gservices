from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.resources as gr  # type: ignore[reportMissingModuleSource]
    from google.oauth2.credentials import Credentials


class SheetsService:
    @staticmethod
    def build(credentials: Credentials, google: GoogleServices) -> SheetsService:
        from googleapiclient.discovery import build  # type: ignore

        resource = build("sheets", "v4", credentials=credentials)
        return SheetsService(resource, google)

    def open(
        self,
        spreadsheet_id: str,
        load: bool = False,
    ) -> Spreadsheet:
        """
        Loads the spreadsheet with ID [spreadsheet_id].

        If the [load] parameter is True, then the grid data for all sheets will also
        be loaded. When the parameter is False (default), only the sheet names and
        their basic properties are loaded. The data can be loaded later on-demand.
        """
        data = (
            self._resource.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, includeGridData=load)
            .execute()
        )
        return Spreadsheet(data, self)

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def __init__(self, resource: gr.SheetsResource, google: GoogleServices):
        self._resource = resource
        self._google = google

    @property
    def resource(self) -> gr.SheetsResource:
        return self._resource


from gservices.google_services import GoogleServices
from gservices.sheets.spreadsheet import Spreadsheet
