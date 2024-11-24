from __future__ import annotations
from typing import TYPE_CHECKING, Literal
from gservices.drive.document_file import DocumentFile
from gservices.drive.file import File
from gservices.drive.spreadsheet_file import SpreadsheetFile

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore


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

    def make_file(
        self, name: str, kind: Literal["spreadsheet", "document", "folder"]
    ) -> File:
        """Creates a new file [name] of the given [kind] within this folder."""
        # fmt: off
        mime_type = (
            SpreadsheetFile.MIME if kind == "spreadsheet" else 
            DocumentFile.MIME if kind == "document" else 
            Folder.MIME
        )
        # fmt: on
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
                    q=f"'{self.id}' in parents and trashed=false",
                    pageToken=page_token,
                    fields=f"files({File.FIELDS})",
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

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[1m{self.name}\033[m/"
        return self.name


from gservices.drive.file_list import FileList
