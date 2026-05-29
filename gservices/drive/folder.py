from typing import TYPE_CHECKING, ClassVar, Literal

from gservices.drive.document_file import DocumentFile
from gservices.drive.file import File

# `SpreadsheetFile` is imported function-local in `make_file()` to avoid a
# circular import: spreadsheet.py -> spreadsheet_file.py -> file.py -> folder.py
# would otherwise try to read SpreadsheetFile from a partially-loaded module.

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore

    from gservices.drive.drive_service import DriveService


class Folder(File):
    MIME = "application/vnd.google-apps.folder"

    def __init__(self, data: g.File, drive: DriveService):
        super().__init__(data, drive)
        self._file_list: FileList | None = None
        assert data.get("mimeType", Folder.MIME) == Folder.MIME

    @property
    def mime_type(self) -> str:
        return Folder.MIME

    def list(self) -> FileList:
        if self._file_list is None:
            self._file_list = self._fetch_files()
        return self._file_list

    _KIND_MIMES: ClassVar[dict[str, str]] = {
        "folder": MIME,
        "document": DocumentFile.MIME,
        "slides": "application/vnd.google-apps.presentation",
        "drawing": "application/vnd.google-apps.drawing",
        # "spreadsheet" added lazily in make_file to avoid an import cycle
        # with `spreadsheet_file.py`.
    }

    def make_file(
        self,
        name: str,
        kind: Literal["spreadsheet", "document", "folder", "slides", "drawing"],
    ) -> File:
        """Creates a new file [name] of the given [kind] within this folder."""
        from gservices.drive.spreadsheet_file import SpreadsheetFile

        if kind == "spreadsheet":
            mime_type = SpreadsheetFile.MIME
        else:
            try:
                mime_type = Folder._KIND_MIMES[kind]
            except KeyError:
                raise ValueError(
                    f"Unknown file kind {kind!r}; expected one of "
                    f"'spreadsheet', 'document', 'folder', 'slides', 'drawing'"
                )
        res = (
            self._drive.resource.files()
            .create(
                body={
                    "name": name,
                    "mimeType": mime_type,
                    "parents": [self.id],
                },
                fields=File.FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )
        new_file = File.resolve_from_mime(res, self._drive)
        self._drive.cache(new_file)
        self.handle_file_added(new_file)
        return new_file

    def _fetch_files(self) -> FileList:
        out = FileList([], self.path)
        drive_id = self.shared_drive_id
        page_token = ""
        while True:
            res = (
                self._drive.resource.files()
                .list(
                    # `self.id` is a Drive file id — `[A-Za-z0-9_-]+` per the
                    # API contract — so direct interpolation into the q-string
                    # is safe (no quote-escaping needed).
                    q=f"'{self.id}' in parents and trashed=false",
                    pageToken=page_token,
                    fields=f"nextPageToken,files({File.FIELDS})",
                    corpora="drive" if drive_id else "user",
                    driveId=drive_id,
                    includeItemsFromAllDrives=bool(drive_id),
                    supportsAllDrives=bool(drive_id),
                )
                .execute()
            )
            for item in res.get("files", []):
                file = File.resolve_from_mime(item, self._drive)
                self._drive.cache(file)
                out.append(file)
            page_token = res.get("nextPageToken", "")
            if not page_token:
                break
        out.sort(key=lambda f: f.name)
        return out

    def handle_file_removed(self, file: File) -> None:
        if self._file_list is not None:
            for i, f in enumerate(self._file_list):
                if f.id == file.id:
                    del self._file_list[i]
                    break

    def handle_file_added(self, file: File) -> None:
        if self._file_list is not None:
            self._file_list.append(file)

    def _invalidate_descendant_paths(self) -> None:
        """
        Called after this folder has been moved or renamed: walks every
        cached descendant, drops its stale `_paths` entry, resets its lazy
        `_path`, and re-caches it under the new (parent-derived) path. The
        descendant `File` instances themselves stay alive in `_ids` so any
        outside reference keeps working.
        """
        if self._file_list is None:
            return
        for child in self._file_list:
            self._drive._uncache_path(child)  # type: ignore[attr-defined]
            child._path = None  # type: ignore[attr-defined]
            child._parent = None  # type: ignore[attr-defined]
            self._drive.cache(child)
            if isinstance(child, Folder):
                child._invalidate_descendant_paths()

    def _uncache_descendants_for_delete(self) -> None:
        """
        Called after this folder has been deleted: recursively removes every
        cached descendant from both `_ids` and `_paths`, and clears the
        cached file list.
        """
        if self._file_list is None:
            return
        for child in self._file_list:
            if isinstance(child, Folder):
                child._uncache_descendants_for_delete()
            self._drive.uncache(child)
        self._file_list = None

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[1m{self.name}\033[m/"
        return self.name


from gservices.drive.file_list import FileList
