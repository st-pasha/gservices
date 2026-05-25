from typing import TYPE_CHECKING

from googleapiclient.discovery import build  # type: ignore

from gservices.json_model import OrjsonModel

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.resources as gr  # type: ignore[reportMissingModuleSource]
    from google.auth.credentials import Credentials

    from gservices.google_services import GoogleServices


class SheetsService:
    @staticmethod
    def build(credentials: Credentials, google: GoogleServices) -> SheetsService:
        resource = build(
            "sheets", "v4", credentials=credentials, model=OrjsonModel()
        )
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
        """Wrap a pre-built `googleapiclient` Sheets v4 resource.

        Typically you don't call this directly — use `SheetsService.build()`
        or the `GoogleServices.Sheets` accessor.
        """
        self._resource = resource
        self._google = google

    @property
    def resource(self) -> gr.SheetsResource:
        """The underlying `googleapiclient` resource. Use to escape-hatch to
        raw API calls that this wrapper doesn't expose."""
        return self._resource


from gservices.sheets.spreadsheet import Spreadsheet
