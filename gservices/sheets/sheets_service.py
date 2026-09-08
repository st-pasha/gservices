from typing import TYPE_CHECKING

from googleapiclient.discovery import build  # type: ignore

from gservices.json_model import OrjsonModel
from gservices.rate_limiter import RateLimiter
from gservices.retrying_http_request import DEFAULT_NUM_RETRIES, RetryingHttpRequest

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.resources as gr  # type: ignore[reportMissingModuleSource]
    from google.auth.credentials import Credentials

    from gservices.google_services import GoogleServices


DEFAULT_REQUESTS_PER_MINUTE = 60
"""
How fast a Sheets service asks by default.

Google's own per-user limit on the Sheets API — 60 read requests per minute,
and separately 60 writes — and the reason this service is paced when Drive and
Gmail are not: theirs are in the thousands, this one is reachable by a single
loop over a workbook's tabs. Raise it to match a raised quota, or pass `None`
to let requests through as fast as they are made.
"""


class SheetsService:
    @staticmethod
    def build(
        credentials: Credentials,
        google: GoogleServices,
        num_retries: int = DEFAULT_NUM_RETRIES,
        requests_per_minute: int | None = DEFAULT_REQUESTS_PER_MINUTE,
    ) -> SheetsService:
        """
        Builds a Sheets v4 service on [credentials].

        Every request it issues retries transient failures up to [num_retries]
        times — see `RetryingHttpRequest` — and waits its turn so that no more
        than [requests_per_minute] start in any minute. The two are
        complementary: pacing keeps the service inside the quota, retrying
        covers the failures that happen anyway.
        """
        limiter = (
            RateLimiter(requests_per_minute)
            if requests_per_minute is not None
            else None
        )
        resource = build(
            "sheets",
            "v4",
            credentials=credentials,
            model=OrjsonModel(),
            requestBuilder=RetryingHttpRequest.builder(num_retries, limiter),
        )
        return SheetsService(resource, google)

    def open(
        self,
        spreadsheet_id: str,
        load: bool = False,
        track_version: bool = False,
        extent: GridExtent = "grid",
    ) -> Spreadsheet:
        """
        Loads the spreadsheet with ID [spreadsheet_id].

        If the [load] parameter is True, then the grid data for all sheets will also
        be loaded. When the parameter is False (default), only the sheet names and
        their basic properties are loaded. The data can be loaded later on-demand.

        [extent] decides how much of a sheet every later grid fetch asks for —
        the whole declared grid, or only the part of it holding data. It is
        worth setting to `"data"` for anything but small documents; see
        `Spreadsheet.extent` for what that trades away.

        If [track_version] is True, the Drive file version is captured as a
        baseline so subsequent `save(check_version=True)` calls can detect
        concurrent edits by other users. Costs one extra Drive API call at
        open time.
        """
        # `includeGridData` is the API's own switch and knows nothing about
        # `extent`, so an eager load goes through the extent-aware path rather
        # than being asked for here.
        data = (
            self._resource.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                includeGridData=load and extent == "grid",
            )
            .execute()
        )
        spreadsheet = Spreadsheet(data, self, extent=extent)
        if track_version:
            spreadsheet._baseline_version = spreadsheet._fetch_drive_version()
        if load and extent != "grid":
            # `include_computed` so that an eager load means the same thing in
            # both modes: `includeGridData=True` carries formula results, and a
            # `cell.value` that reads `None` only under one extent would be a
            # trap rather than a trade.
            spreadsheet._load_all_data(include_computed=True)
        return spreadsheet

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


from gservices.sheets.spreadsheet import GridExtent, Spreadsheet
