import datetime as dt
import json
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from gservices.sheets.utils import (
    color_object_to_string,
    color_string_to_object,
    coords_to_address,
    merge_requests,
    quote_sheet_title,
    set_dotted_property,
)

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

    from gservices.sheets.snapshot import SpreadsheetSnapshot


GridExtent = Literal["grid", "data"]
"""
How much of a sheet a grid fetch asks for. See `Spreadsheet.extent`.
"""


# Description prefix for protectedRanges this library creates as edit-locks.
# Distinguishes our locks from user-created protections so stale-lock cleanup
# only touches what we own.
_LOCK_MARKER = "gservices-lock:"


def _cell_fields(include_computed: bool) -> str:
    """The `CellData` sub-fields a grid fetch asks for, as a `fields=` list."""
    fields = ["userEnteredValue", "effectiveFormat", "note", "hyperlink"]
    if include_computed:
        fields.insert(1, "effectiveValue")
    return ",".join(fields)


class SpreadsheetVersionMismatchError(RuntimeError):
    """Raised by `Spreadsheet.save(check_version=True)` when the spreadsheet
    has been modified externally since it was loaded (or since the last
    successful save).

    Attributes:
      - baseline: the Drive file version the local state was based on.
      - current: the current Drive file version on the server.
    """

    def __init__(self, baseline: int, current: int):
        super().__init__(
            f"Spreadsheet has been modified externally "
            f"(baseline version {baseline}, current {current}). "
            f"Re-open the spreadsheet and reapply your changes."
        )
        self.baseline = baseline
        self.current = current


class Spreadsheet:
    """
    [Spreadsheet] represents a single Google Sheets document, stored on Google Drive.

    In order to open a Spreadsheet, you need the ID of the underlying document, and
    then call [SheetsService.open()]:

        spreadsheet = google_service.Sheets.open(spreadsheet_id)

    Spreadsheet properties and cell values can be modified through this object. All such
    changes will be queued until you run [save()], at which point they will be uploaded
    to the server in one or more batches.

    Concurrency: to detect concurrent edits by other users, open with
    `track_version=True` and call `save(check_version=True)`. The save will
    raise `SpreadsheetVersionMismatchError` if the file's Drive version has
    changed since load (or the last successful save).
    """

    BATCH_SIZE = 500

    def __init__(
        self,
        data: gs.Spreadsheet,
        service: SheetsService,
        extent: GridExtent = "grid",
    ):
        self._service = service
        self._extent: GridExtent = extent
        self._id: str = data.get("spreadsheetId", "")
        self._url: str = data.get("spreadsheetUrl", "")
        self._properties: gs.SpreadsheetProperties = data.get("properties", {})
        self._metadata = SpreadsheetDeveloperMetadata(
            data.get("developerMetadata", []), self
        )
        self._sheets = [
            Sheet(data=item, spreadsheet=self) for item in data.get("sheets", [])
        ]
        # The list of all updates that are scheduled to be applied to the spreadsheet
        # on the next `save()`.
        self._pending_updates: list[gs.Request] = []
        self._pending_callbacks: list[Callable[[gs.Response], None] | None] = []
        # Drive file version we believe the local state is based on, set when
        # the spreadsheet is opened with `track_version=True`. `None` means
        # tracking is disabled — `save(check_version=True)` will raise.
        self._baseline_version: int | None = None

    def _load_all_data(
        self,
        include_computed: bool = False,
        only_sheets: list[Sheet] | None = None,
    ) -> None:
        """Loads cell data for every sheet that doesn't yet have it, batched
        across sheets. Much faster than letting each sheet hit the API
        independently when the spreadsheet has many sheets.

        A `fields=` mask restricts the response to the subset of CellData /
        sheet data that the snapshot builder reads — skipping `formattedValue`,
        `userEnteredFormat`, `textFormatRuns`, validation, pivot tables, etc.
        For typical formatted documents this halves the response size.

        How much of each sheet is fetched depends on the `extent` the
        spreadsheet was opened with — the whole declared grid, or only the
        populated part of it.

        `only_sheets`, if given, limits which sheets are considered candidates
        for loading — used by `reload()` to avoid eager-loading sheets that
        the caller never asked for.
        """
        candidates = only_sheets if only_sheets is not None else self._sheets
        missing = [sheet for sheet in candidates if sheet._cell_data is None]
        if not missing:
            return
        if self._extent == "data":
            self._load_within_data_extent(missing, include_computed)
        else:
            self._load_whole_grid(missing, include_computed)

    def _load_whole_grid(self, missing: list[Sheet], include_computed: bool) -> None:
        """Loads every cell of the sheets' declared grids, in one call."""
        fields = (
            "sheets("
            "properties.sheetId,"
            "data("
            "rowMetadata,columnMetadata,"
            f"rowData.values({_cell_fields(include_computed)})"
            ")"
            ")"
        )
        data = (
            self._service.resource.spreadsheets()
            .get(spreadsheetId=self._id, includeGridData=True, fields=fields)
            .execute()
        )
        by_id = {sheet.id: sheet for sheet in self._sheets}
        for sheet_data in data.get("sheets", []):
            sheet_id = sheet_data.get("properties", {}).get("sheetId")
            if sheet_id is None:
                continue
            sheet = by_id.get(sheet_id)
            if sheet is not None and sheet._cell_data is None:
                blocks = sheet_data.get("data", [])
                if blocks:
                    sheet._cell_data = blocks[0]

    def _load_within_data_extent(
        self, missing: list[Sheet], include_computed: bool
    ) -> None:
        """
        Loads only the populated corner of each sheet — see `extent`.

        Three requests where the whole-grid path makes one, and far cheaper
        than it anyway: what dominates a grid fetch is the `effectiveFormat`
        that comes back for *every* cell inside the declared grid, empty ones
        included, and a formatted-but-empty cell's format is several kilobytes
        of nested objects once parsed.

        1. `values.batchGet` — the values themselves, which is also the only
           way to ask the API where the data ends. The answer is kept as each
           sheet's `values` cache rather than thrown away.
        2. `spreadsheets.get`, bounded to `A1:<last cell>` per sheet, for the
           formats, notes and formulas the values call does not carry.
        3. `spreadsheets.get` for `rowMetadata` / `columnMetadata` alone,
           **unbounded**. Row properties and developer metadata are the one
           thing that lives outside the data extent on purpose — an id pinned
           to an empty row so that whatever is typed there is already
           identified — and bounding them away would mean re-writing that id
           on every load, for ever.
        """
        extents = self._fetch_value_extents(missing)
        grids = self._fetch_bounded_grids(missing, extents, include_computed)
        dimensions = self._fetch_dimension_properties()
        for sheet in missing:
            block: dict[str, Any] = {}
            row_data = grids.get(sheet.id)
            if row_data is not None:
                block["rowData"] = row_data
            block.update(dimensions.get(sheet.id, {}))
            # Set even when empty: `None` means "not loaded yet", and a sheet
            # left that way would ask for itself again on the next access.
            sheet._cell_data = cast("gs.GridData", block)

    def _fetch_value_extents(
        self, sheets: list[Sheet]
    ) -> dict[int, tuple[int, int]]:
        """
        How far the data reaches in each of [sheets], as `{id: (rows, cols)}`.

        `values.batchGet` reports the populated range and nothing else, so this
        is a small response however large the grid is. The values are kept: the
        caller has just paid for them, and dropping them would make the next
        `sheet.values` fetch the same bytes again.
        """
        response = (
            self._service.resource.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=self._id,
                ranges=[quote_sheet_title(sheet.title) for sheet in sheets],
                fields="valueRanges(values)",
            )
            .execute()
        )
        ranges = response.get("valueRanges", [])
        extents: dict[int, tuple[int, int]] = {}
        # `strict`: the API answers one range per range asked for, and a short
        # answer would otherwise leave the tail of the workbook looking empty
        # rather than unread — which is the one failure this mode must not have.
        for sheet, value_range in zip(sheets, ranges, strict=True):
            values = cast(list[list[str]], value_range.get("values", []))
            n_cols = max((len(row) for row in values), default=0)
            extents[sheet.id] = (len(values), n_cols)
            if sheet._cell_values is None:
                for row in values:
                    if len(row) < n_cols:
                        row += [""] * (n_cols - len(row))
                sheet._cell_values = values
        return extents

    def _fetch_bounded_grids(
        self,
        sheets: list[Sheet],
        extents: dict[int, tuple[int, int]],
        include_computed: bool,
    ) -> dict[int, list[gs.RowData]]:
        """The `rowData` of each sheet's populated range, as `{id: rows}`."""
        ranges: list[str] = []
        for sheet in sheets:
            n_rows, n_cols = extents.get(sheet.id, (0, 0))
            if n_rows == 0 or n_cols == 0:
                continue
            last = coords_to_address(n_rows - 1, n_cols - 1)
            ranges.append(f"{quote_sheet_title(sheet.title)}!A1:{last}")
        if not ranges:
            return {}
        fields = (
            "sheets("
            "properties.sheetId,"
            f"data(rowData.values({_cell_fields(include_computed)}))"
            ")"
        )
        data = (
            self._service.resource.spreadsheets()
            .get(
                spreadsheetId=self._id,
                ranges=ranges,
                includeGridData=True,
                fields=fields,
            )
            .execute()
        )
        grids: dict[int, list[gs.RowData]] = {}
        for sheet_data in data.get("sheets", []):
            sheet_id = sheet_data.get("properties", {}).get("sheetId")
            blocks = sheet_data.get("data", [])
            if sheet_id is None or not blocks:
                continue
            grids[sheet_id] = blocks[0].get("rowData", [])
        return grids

    def _fetch_dimension_properties(self) -> dict[int, dict[str, Any]]:
        """
        Every sheet's row and column properties, as `{id: {...}}`.

        Asked for on its own, over the whole grid, because these are what the
        data extent must not cut off — see `_load_within_data_extent`. Without
        `rowData` in the mask the response is a few hundred kilobytes for a
        workbook whose full grid is hundreds of megabytes.
        """
        data = (
            self._service.resource.spreadsheets()
            .get(
                spreadsheetId=self._id,
                includeGridData=True,
                fields="sheets(properties.sheetId,data(rowMetadata,columnMetadata))",
            )
            .execute()
        )
        properties: dict[int, dict[str, Any]] = {}
        for sheet_data in data.get("sheets", []):
            sheet_id = sheet_data.get("properties", {}).get("sheetId")
            blocks = sheet_data.get("data", [])
            if sheet_id is None or not blocks:
                continue
            block = cast(dict[str, Any], blocks[0])
            properties[sheet_id] = {
                key: block[key]
                for key in ("rowMetadata", "columnMetadata")
                if key in block
            }
        return properties

    def save(self, check_version: bool = False) -> None:
        """
        Saves any pending changes to the spreadsheet file stored in Google Cloud.

        If [check_version] is True, the Drive file version is fetched and
        compared against the baseline captured at load time (see
        `SheetsService.open(track_version=True)`). If the version has changed,
        `SpreadsheetVersionMismatchError` is raised and no changes are sent
        to the server.

        Note: `save(check_version=True)` is best-effort, not transactional.
        The Sheets API has no compare-and-swap primitive — a remote edit
        landing between the version check and the batchUpdate will not be
        caught. The window is small (a single round-trip) but non-zero.
        """
        if check_version:
            if self._baseline_version is None:
                raise ValueError(
                    "save(check_version=True) requires version tracking — "
                    "open the spreadsheet with track_version=True"
                )
            current = self._fetch_drive_version()
            if current != self._baseline_version:
                raise SpreadsheetVersionMismatchError(
                    baseline=self._baseline_version, current=current
                )
        if not self._pending_updates:
            return
        i0 = 0
        while i0 < len(self._pending_updates):
            updates = self._pending_updates[i0 : i0 + Spreadsheet.BATCH_SIZE]
            response = (
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
            replies = response.get("replies", [])
            for i, reply in enumerate(replies):
                callback = self._pending_callbacks[i + i0]
                if callback:
                    callback(reply)
            i0 += Spreadsheet.BATCH_SIZE
        self._pending_updates = []
        self._pending_callbacks = []
        if self._baseline_version is not None:
            # Refresh the baseline so the next checked save compares against
            # the post-save server state.
            self._baseline_version = self._fetch_drive_version()

    def _fetch_drive_version(self) -> int:
        """Fetches the current Drive file version. Used for `track_version`
        baseline capture and `check_version` enforcement."""
        drive_resource = self._service._google.Drive.resource
        data = (
            drive_resource.files()
            .get(fileId=self._id, fields="version", supportsAllDrives=True)
            .execute()
        )
        return int(data.get("version", 0))

    def reload(self, include_computed: bool = False) -> None:
        """
        Re-fetches cell data for every sheet that already had data loaded,
        in a single batched API call. Local cell data, the formatted-values
        cache, and the cell cache are replaced with authoritative server
        state.

        Sheets that were never loaded are NOT eager-loaded (their lazy-load
        on next access still works as before). To force-load everything,
        open the spreadsheet with `Sheets.open(id, load=True)` instead.

        IMPORTANT: any `Cell`, `Row`, or `Column` references obtained
        before `reload()` become stale — their backing `_data` points
        to the discarded `_cell_data`, and reads/writes through them
        produce undefined results. Re-fetch via `sheet.cell(...)`,
        `sheet.rows[...]`, `sheet.columns[...]` after `reload()`.

        Typical use is inside `exclusive_edit()` — protection only blocks
        future edits, so anything loaded *before* the lock might be stale
        relative to edits that landed in the window before the lock took
        effect. Reload inside the block to get a clean baseline.

        Does NOT refresh: spreadsheet properties (title, theme, locale),
        sheet developer metadata, merged ranges, protected ranges, or any
        sheet sub-object this wrapper doesn't model. For a fully fresh
        view, re-open via `Sheets.open(id)`.
        """
        loaded = [s for s in self._sheets if s._cell_data is not None]
        if not loaded:
            return
        for sheet in loaded:
            sheet._invalidate()
        self._load_all_data(
            include_computed=include_computed,
            only_sheets=loaded,
        )

    @contextmanager  # pyright: ignore[reportDeprecated]
    def exclusive_edit(
        self,
        *,
        sheets: Sequence["Sheet | str"] | None = None,
        ttl_seconds: int = 300,
    ) -> Iterator[None]:
        """
        Acquire exclusive edit access on the spreadsheet for the duration of
        the `with` block, via Sheets' `addProtectedRange` mechanism.

        Adds a `protectedRange` covering each target sheet with the
        authenticated user as the sole editor. Other users see the affected
        sheets as read-only (a lock icon + "you don't have permission to
        edit" warning) until the block exits.

        Normal exit: any pending updates are flushed via `save()`, then the
        protection is released. Exception exit: the protection is released
        but pending updates are NOT flushed — the caller decides what to
        do with them (e.g. discard, retry).

        Stale-lock recovery: each acquire encodes `{holder, lock_id,
        expires_at}` JSON in the protection's `description` field. Before
        installing its own lock, the next `exclusive_edit()` scans existing
        protections and forcibly removes any of our locks whose
        `expires_at` is in the past — so a crashed run self-heals after
        `ttl_seconds`.

        Limitations:
          - Best-effort, not transactional. The Sheets API has no
            compare-and-swap, so two simultaneous `exclusive_edit()` calls
            can both install protections (each thinks it holds the lock).
            Server-side edit blocking still applies to non-listed users
            for both protections.
          - Other users see the protection in their UI ("locked by X"),
            which is intrusive for collaborative spreadsheets.
          - Requires the authenticated user to be the file owner or have
            edit access; an owner can still remove the protection manually.
          - Setting `sheets=None` (default) protects all sheets, including
            ones you don't intend to edit. Pass a subset to scope down.

        Args:
          sheets: list of Sheet or sheet-name strings to lock. None
            (default) locks every sheet in the spreadsheet.
          ttl_seconds: how long after acquisition the lock is considered
            "stale" by a future caller's recovery sweep. The lock is NOT
            automatically released at this time — only marked as available
            for forced removal. Default 300s (5 min). Set generously.
        """
        target_sheets = self._resolve_lock_targets(sheets)
        my_email = self._fetch_self_email()

        existing = self._fetch_protected_ranges()
        stale_ids = self._find_stale_lock_ids(existing)

        lock_id = str(uuid.uuid4())
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=ttl_seconds)
        description = _LOCK_MARKER + json.dumps({
            "holder": my_email,
            "lock_id": lock_id,
            "expires_at": expires_at.isoformat(),
        })
        acquire_requests: list[gs.Request] = [
            {"deleteProtectedRange": {"protectedRangeId": pid}}
            for pid in stale_ids
        ]
        for sheet in target_sheets:
            acquire_requests.append({
                "addProtectedRange": {
                    "protectedRange": {
                        "range": {"sheetId": sheet.id},
                        "description": description,
                        "editors": {"users": [my_email]},
                    },
                },
            })

        response = (
            self._service.resource.spreadsheets()
            .batchUpdate(
                spreadsheetId=self._id,
                body={
                    "requests": acquire_requests,
                    "includeSpreadsheetInResponse": False,
                },
            )
            .execute()
        )

        # Pluck the new protectedRangeIds from the addProtectedRange replies
        # (deleteProtectedRange replies are empty objects we don't need).
        our_protection_ids: list[int] = []
        for reply in response.get("replies", []):
            add = reply.get("addProtectedRange")
            if add is None:
                continue
            pid = add.get("protectedRange", {}).get("protectedRangeId")
            if pid is not None:
                our_protection_ids.append(pid)

        try:
            yield
            # Normal exit: flush user-queued writes before releasing.
            self.save()
        finally:
            # Always release our protections — even on exception. Send a
            # direct batchUpdate (don't go through self._pending_updates,
            # which may still hold unflushed user requests).
            if our_protection_ids:
                release_requests: list[gs.Request] = [
                    {"deleteProtectedRange": {"protectedRangeId": pid}}
                    for pid in our_protection_ids
                ]
                self._service.resource.spreadsheets().batchUpdate(
                    spreadsheetId=self._id,
                    body={
                        "requests": release_requests,
                        "includeSpreadsheetInResponse": False,
                    },
                ).execute()

    def _resolve_lock_targets(
        self, sheets: "Sequence[Sheet | str] | None"
    ) -> list[Sheet]:
        if sheets is None:
            return list(self._sheets)
        out: list[Sheet] = []
        for s in sheets:
            if isinstance(s, str):
                obj = self.sheet(s)
                if obj is None:
                    raise KeyError(f"Sheet {s!r} not found in spreadsheet")
                out.append(obj)
            else:
                out.append(s)
        return out

    def _fetch_self_email(self) -> str:
        """Returns the authenticated principal's email (OAuth user or
        service account address). Cached on the SheetsService."""
        drive_resource = self._service._google.Drive.resource
        data = cast(dict[str, Any], drive_resource.about().get(fields="user").execute())
        return data["user"]["emailAddress"]

    def _fetch_protected_ranges(self) -> list[tuple[int, dict[str, Any]]]:
        """Returns (sheet_id, protectedRange) tuples for every existing
        protectedRange in this spreadsheet — used to find stale locks
        and to leave non-lock protections alone."""
        data = (
            self._service.resource.spreadsheets()
            .get(
                spreadsheetId=self._id,
                fields="sheets.properties.sheetId,sheets.protectedRanges",
            )
            .execute()
        )
        out: list[tuple[int, dict[str, Any]]] = []
        for sheet in cast(list[dict[str, Any]], data.get("sheets", [])):
            sheet_id = sheet.get("properties", {}).get("sheetId", -1)
            for pr in sheet.get("protectedRanges", []):
                out.append((sheet_id, pr))
        return out

    def _find_stale_lock_ids(
        self, existing: list[tuple[int, dict[str, Any]]]
    ) -> list[int]:
        """Filter to protectedRangeIds whose description carries our lock
        marker and an expires_at in the past. Malformed metadata is left
        alone (could be a future lock format we don't recognize)."""
        now = dt.datetime.now(dt.UTC)
        stale: list[int] = []
        for _, pr in existing:
            description = pr.get("description", "")
            if not description.startswith(_LOCK_MARKER):
                continue
            try:
                metadata = json.loads(description[len(_LOCK_MARKER):])
                expires_at = dt.datetime.fromisoformat(metadata["expires_at"])
                if expires_at < now:
                    pid = pr.get("protectedRangeId")
                    if pid is not None:
                        stale.append(pid)
            except (ValueError, KeyError, TypeError):
                continue
        return stale

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
    def extent(self) -> GridExtent:
        """
        How much of each sheet this spreadsheet fetches when it loads a grid.

        `"grid"`, the default, loads the sheet's declared grid — every cell a
        sheet *has*, which for a blank one is 1000 × 26 and for a sheet someone
        has widened can be far more. `"data"` loads only the rectangle that
        holds values, plus the row and column properties of the whole sheet.

        Prefer `"data"` unless the formatting of empty cells matters to you.
        The declared grid is mostly empty in real documents, and an empty cell
        still comes back carrying the format it inherits, so the difference is
        not marginal: a 29-tab delivery log measured 1,044,206 cells and 7.5 GB
        of resident memory to snapshot whole, against 13,015 cells to snapshot
        within its data extent — the same 0.5 MB snapshot either way, since
        what the extra million cells contribute is a format identical to their
        neighbours'.

        What `"data"` gives up is exactly that: a cell holding no value but a
        deliberate format — a colour-coded empty column, a ruled-off block
        below a table — is outside the extent and does not reach the snapshot.
        Row heights, hidden rows, and developer metadata are *not* given up;
        those are read for the whole sheet either way.

        Set when the spreadsheet is opened, and fixed thereafter, so that every
        grid this object loads is bounded the same way:

            spreadsheet = google.Sheets.open(file_id, extent="data")
        """
        return self._extent

    @property
    def metadata(self) -> SpreadsheetDeveloperMetadata:
        """
        Metadata associated with the spreadsheet; this object can be used to query
        existing metadata, update it, or create new metadata records.
        """
        return self._metadata

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
        """Print a human-readable summary of this spreadsheet to stdout (debug aid)."""
        pprint("[bold cyan]Spreadsheet:")
        pprint(f"  [green]title:[/] [bold white]{self.title}")
        pprint(f"  [green]id:[/] {self.id}")
        pprint(f"  [green]url:[/] {self.url}")
        pprint(f"  [green]locale:[/] {self.locale}")
        pprint(f"  [green]time_zone:[/] {self.time_zone}")
        pprint("  [green]theme:[/]")
        pprint(f"    [green]font_family:[/] {self.theme.get('primaryFontFamily')}")
        pprint("    [green]colors:[/]")
        for record in self.theme.get("themeColors", []):
            color = color_object_to_string(record.get("color", {}))
            pprint(f"      [green]{record.get('colorType')}:[/] {color}")
        pprint("  [green]cell_format:")
        self.default_cell_format.print(indent="    ")
        pprint("  [green]sheets:")
        for sheet in self.sheets:
            pprint(
                f"    [magenta not bold]\\[{sheet.index}][/]: "
                f"[bold white]{sheet.title}[/], id={sheet.id}"
            )
        pprint("  [green]metadata:")
        self.metadata.print(indent="    ")

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

    def add_sheet(
        self,
        name: str,
        *,
        row_count: int | None = None,
        column_count: int | None = None,
        tab_color: str | None = None,
        hide_gridlines: bool = False,
    ) -> Sheet:
        """
        Creates a new sheet with the given [name] and adds it at the end of the
        sheet list.

        Optional grid sizing: pass [row_count] and/or [column_count] to override
        the API's defaults. Pass [tab_color] (hex string or theme name) to set
        the tab color, and [hide_gridlines]=True to hide gridlines in the new
        sheet.
        """
        max_id = max((sheet.id for sheet in self._sheets), default=-1)
        properties: gs.SheetProperties = {
            "sheetId": max_id + 1,
            "sheetType": "GRID",
            "title": name,
            "index": len(self._sheets),
        }
        grid_properties: gs.GridProperties = {}
        if row_count is not None:
            grid_properties["rowCount"] = row_count
        if column_count is not None:
            grid_properties["columnCount"] = column_count
        if hide_gridlines:
            grid_properties["hideGridlines"] = True
        if grid_properties:
            properties["gridProperties"] = grid_properties
        if tab_color is not None:
            properties["tabColorStyle"] = color_string_to_object(tab_color)

        sheet = Sheet({"properties": properties}, self)

        def _on_add_response(response: gs.Response) -> None:
            new_props = response.get("addSheet", {}).get("properties", {})
            actual_id = new_props.get("sheetId")
            if actual_id is not None:
                sheet._properties["sheetId"] = actual_id

        self._add_request(
            {"addSheet": {"properties": properties}},
            callback=_on_add_response,
        )
        self._sheets.append(sheet)
        return sheet

    def delete_sheet(self, sheet: Sheet | str) -> None:
        """
        Deletes the given [sheet] from the spreadsheet.
        """
        if isinstance(sheet, str):
            resolved = self.sheet(sheet)
            if resolved is None:
                raise KeyError(f"Sheet `{sheet}` does not exist in the spreadsheet")
        else:
            resolved = sheet
        assert resolved._spreadsheet is self
        resolved.delete()

    def snapshot(self, include_computed: bool = False) -> SpreadsheetSnapshot:
        """
        Builds a layered, human-readable snapshot of the spreadsheet's current state.

        Triggers a full grid load on each sheet if cell data isn't loaded yet.
        Set `include_computed=True` to capture formula results in a separate
        `computed` map per sheet; by default this is omitted to keep snapshots
        stable across volatile formulas (NOW, RAND, IMPORTRANGE, etc.).
        """
        from gservices.sheets.snapshot import build_snapshot
        return build_snapshot(self, include_computed=include_computed)

    def save_snapshot(
        self,
        path: str | Path,
        include_computed: bool = False,
    ) -> None:
        """
        Writes a snapshot of this spreadsheet to disk as JSON.

        The on-disk layout is optimized for `git diff` readability: each data
        row, format entry, and border segment occupies a single line.
        """
        from gservices.sheets.snapshot import write_snapshot
        write_snapshot(self.snapshot(include_computed=include_computed), path)

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
        callback: Callable[[gs.Response], None] | None = None,
    ) -> None:
        if not callback and self._pending_updates:
            previous_request = self._pending_updates[-1]
            if merge_requests(previous_request, request):
                return
        self._pending_updates.append(request)
        self._pending_callbacks.append(callback)

    def _check_integrity(self) -> None:
        for i, sheet in enumerate(self.sheets):
            assert sheet._spreadsheet is self
            assert sheet.index == i
            sheet._check_integrity()


from gservices.drive.spreadsheet_file import SpreadsheetFile
from gservices.print_utils import pprint
from gservices.sheets.cell_format import CellFormat
from gservices.sheets.developer_metadata import SpreadsheetDeveloperMetadata
from gservices.sheets.sheet import Sheet
from gservices.sheets.sheets_service import SheetsService
