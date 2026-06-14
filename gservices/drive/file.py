import datetime as dt
import mimetypes
import pathlib
from io import BytesIO
from typing import TYPE_CHECKING, cast

from googleapiclient.http import MediaIoBaseUpload  # type: ignore

from gservices.drive.path import Path

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore

    from gservices.drive.drive_service import DriveService

# Content accepted by `upload` / `update_content`: raw bytes, a text string
# (encoded as UTF-8), or a filesystem path whose bytes are read in.
Content = bytes | str | pathlib.Path


class File:
    FIELDS = "id,name,mimeType,parents,driveId,shortcutDetails,contentRestrictions"

    def __init__(self, data: g.File, drive: DriveService):
        self._data = data
        self._drive = drive
        self._path: Path | None = None
        self._parent: Folder | None = None
        self._shared_drive_id: str | None = None
        # True once a `fields=*` fetch has populated the extended properties
        # (size, createdTime, modifiedTime, ...). Folders / Workspace docs do
        # not return a "size" field even after a full fetch, so we can't use
        # the presence of "size" as a load sentinel.
        self._loaded: bool = False

    @staticmethod
    def resolve_from_mime(data: g.File, service: DriveService) -> File:
        # Imported here (not at module level) to break the cycle with
        # spreadsheet_file.py, which itself imports File.
        from gservices.drive.spreadsheet_file import SpreadsheetFile

        mime_type = data.get("mimeType", "")
        if mime_type == Folder.MIME:
            cls = Folder
        elif mime_type == Shortcut.MIME:
            cls = Shortcut
        elif mime_type == SpreadsheetFile.MIME:
            cls = SpreadsheetFile
        elif mime_type == DocumentFile.MIME:
            cls = DocumentFile
        else:
            cls = File
        return cls(data, service)

    @staticmethod
    def _coerce_content(data: Content, name: str | None) -> tuple[bytes, str]:
        """
        Normalize [data] into raw bytes plus an inferred MIME type.

        - `pathlib.Path` -> the file's bytes; MIME guessed from the path's name.
        - `str`          -> UTF-8 bytes; MIME guessed from [name], else text/plain.
        - `bytes`        -> used as-is; MIME guessed from [name], else octet-stream.

        [name] is the eventual Drive file name, used as the primary hint for
        MIME inference.
        """
        if isinstance(data, pathlib.Path):
            content = data.read_bytes()
            mime = mimetypes.guess_type(name or data.name)[0]
        elif isinstance(data, str):
            content = data.encode("utf-8")
            mime = (mimetypes.guess_type(name)[0] if name else None) or "text/plain"
        else:
            content = bytes(data)
            mime = mimetypes.guess_type(name)[0] if name else None
        return content, mime or "application/octet-stream"

    # ----------------------------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @property
    def mime_type(self) -> str:
        return self._data.get("mimeType", "")

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = self.parent.path / self.name
        return self._path

    @property
    def parent(self) -> Folder:
        if self._parent is None:
            if "parents" in self._data:
                parent_id = self._data["parents"][0]
            else:
                parent_id = ""
            parent = self._drive.get(id=parent_id)
            assert isinstance(parent, Folder)
            self._parent = parent
        return self._parent

    @property
    def is_dir(self) -> bool:
        return isinstance(self, Folder)

    @property
    def is_shared_drive(self) -> bool:
        return isinstance(self, SharedDrive)

    @property
    def is_shortcut(self) -> bool:
        return isinstance(self, Shortcut)

    @property
    def is_spreadsheet(self) -> bool:
        from gservices.drive.spreadsheet_file import SpreadsheetFile

        return isinstance(self, SpreadsheetFile)

    @property
    def is_document(self) -> bool:
        return isinstance(self, DocumentFile)

    @property
    def shared_drive_id(self) -> str:
        """
        Id of the shared drive on which this file is located, or empty string if the
        file is not on a shared drive.
        """
        if self._shared_drive_id is None:
            # Prefer the server-supplied `driveId` (single field) over walking
            # up to the nearest SharedDrive ancestor, which would N-round-trip
            # for a deep file fetched by id with cold ancestors.
            if "driveId" in self._data:
                self._shared_drive_id = self._data["driveId"]
            else:
                self._shared_drive_id = self.parent.shared_drive_id
        return self._shared_drive_id

    # ----------------------------------------------------------------------------------
    # Extended properties
    # ----------------------------------------------------------------------------------

    @property
    def size(self) -> int:
        """
        Size in bytes of blobs and first party editor files. Will be 0 for files
        that have no size, like shortcuts and folders.
        """
        self._ensure_all_properties_loaded()
        return int(self._data.get("size", 0))

    @property
    def created_time(self) -> dt.datetime:
        """The time at which the file was created (RFC 3339 date-time)."""
        self._ensure_all_properties_loaded()
        timestamp = self._data.get("createdTime", "")
        return dt.datetime.fromisoformat(timestamp)

    @property
    def modified_time(self) -> dt.datetime:
        """The last time the file was modified by anyone (RFC 3339 date-time)."""
        self._ensure_all_properties_loaded()
        timestamp = self._data.get("modifiedTime", "")
        return dt.datetime.fromisoformat(timestamp)

    @property
    def starred(self) -> bool:
        """Whether the user has starred the file."""
        self._ensure_all_properties_loaded()
        return self._data.get("starred", False)

    @property
    def trashed(self) -> bool:
        """
        Whether the file has been trashed, either explicitly or from a trashed
        parent folder. Only the owner may trash a file, and other users cannot
        see files in the owner's trash.
        """
        self._ensure_all_properties_loaded()
        return self._data.get("trashed", False)

    @property
    def explicitly_trashed(self) -> bool:
        """
        Whether the file has been explicitly trashed, as opposed to recursively
        trashed from a parent folder.
        """
        self._ensure_all_properties_loaded()
        return self._data.get("explicitlyTrashed", False)

    @property
    def version(self) -> int:
        """
        A monotonically increasing version number for the file. This reflects
        every change made to the file on the server, even those not visible to
        the user.
        """
        self._ensure_all_properties_loaded()
        return int(self._data.get("version", 0))

    def _ensure_all_properties_loaded(self):
        if not self._loaded:
            new_data = (
                self._drive.resource.files()
                .get(fileId=self.id, fields="*", supportsAllDrives=True)
                .execute()
            )
            self._data = new_data
            self._loaded = True

    # ----------------------------------------------------------------------------------
    # Public methods
    # ----------------------------------------------------------------------------------

    def rename(self, new_name: str) -> None:
        res = (
            self._drive.resource.files()
            .update(
                fileId=self.id,
                body={"name": new_name},
                fields=File.FIELDS,
                supportsAllDrives=bool(self.shared_drive_id),
            )
            .execute()
        )
        self._drive.uncache(self)
        self._path = None
        self._data = res
        self._drive.cache(self)
        if isinstance(self, Folder):
            self._invalidate_descendant_paths()

    def move_to(self, dest: Path) -> None:
        current_parent = self.parent
        new_parent_file = self._drive.get(dest)
        if not new_parent_file.is_dir:
            raise NotADirectoryError(f"The target path `{dest}` is not a directory")
        new_parent = cast(Folder, new_parent_file)
        if new_parent.id == current_parent.id:
            return
        res = (
            self._drive.resource.files()
            .update(
                fileId=self.id,
                addParents=new_parent.id,
                removeParents=current_parent.id,
                fields=File.FIELDS,
                supportsAllDrives=bool(self.shared_drive_id),
            )
            .execute()
        )
        current_parent.handle_file_removed(self)
        new_parent.handle_file_added(self)
        self._drive.uncache(self)
        self._path = None
        self._parent = None
        self._shared_drive_id = None
        self._data = res
        self._drive.cache(self)
        if isinstance(self, Folder):
            self._invalidate_descendant_paths()

    def copy_to(self, dest: Path) -> None:
        dest_dir = self._drive.get(dest.parent)
        if not isinstance(dest_dir, Folder):
            raise NotADirectoryError(
                f"Destination path `{dest_dir.path}` is not a directory"
            )
        if isinstance(self, Folder):
            # Drive's `files.copy` refuses folders (returns "The resource
            # body includes fields which are not directly writable" or
            # creates an empty folder, depending on permissions). Emulate a
            # recursive copy: create the destination folder, then copy each
            # child into it.
            new_folder = dest_dir.make_file(dest.basename, "folder")
            for child in self.list():
                child.copy_to(new_folder.path / child.name)
            return
        res = (
            self._drive.resource.files()
            .copy(
                fileId=self.id,
                fields=File.FIELDS,
                supportsAllDrives=True,
                body={
                    "name": dest.basename,
                    "parents": [dest_dir.id],
                },
            )
            .execute()
        )
        new_file = File.resolve_from_mime(res, self._drive)
        dest_dir.handle_file_added(new_file)
        self._drive.cache(new_file)

    def delete(self, trash: bool = True) -> None:
        """
        Deletes the file, putting it into the Trash if the [trash] flag is True, or
        deleting permanently if the flag is False. If the file is a folder that has
        other files inside, those other files will be deleted recursively.
        """
        if trash:
            self._drive.resource.files().update(
                fileId=self.id, body={"trashed": True}, supportsAllDrives=True
            ).execute()
        else:
            self._drive.resource.files().delete(
                fileId=self.id, supportsAllDrives=True
            ).execute()  # type: ignore
        if isinstance(self, Folder):
            self._uncache_descendants_for_delete()
        self._drive.uncache(self)
        self.parent.handle_file_removed(self)

    def download(self) -> bytes:
        """
        Download and return the raw content bytes of this file.

        Only works for binary "blob" files (PDFs, images, JSON, ...). Workspace
        items — folders, Sheets, Docs, Slides, Drawings, shortcuts — have no
        downloadable bytes; call `DriveService.resource` to export them instead.
        """
        if self.mime_type.startswith("application/vnd.google-apps."):
            raise ValueError(
                f"Cannot download `{self.name}`: it is a Workspace item "
                f"({self.mime_type}) with no raw byte content. Export it via "
                f"`DriveService.resource` instead."
            )
        return (
            self._drive.resource.files()
            .get_media(fileId=self.id, supportsAllDrives=True)
            .execute()
        )

    def update_content(self, data: Content, *, mime_type: str | None = None) -> None:
        """
        Overwrite this file's content in place with [data] (bytes, a text string,
        or a `pathlib.Path` to read from). The file's id, name, and path are
        unchanged. [mime_type] overrides the inferred MIME type.

        Only works for binary "blob" files; Workspace items have no raw bytes.
        """
        if self.mime_type.startswith("application/vnd.google-apps."):
            raise ValueError(
                f"Cannot update content of `{self.name}`: it is a Workspace item "
                f"({self.mime_type}) with no raw byte content."
            )
        content, inferred = File._coerce_content(data, self.name)
        media = MediaIoBaseUpload(
            BytesIO(content), mimetype=mime_type or inferred, resumable=False
        )
        res = (
            self._drive.resource.files()
            .update(
                fileId=self.id,
                media_body=media,
                fields=File.FIELDS,
                supportsAllDrives=bool(self.shared_drive_id),
            )
            .execute()
        )
        self._data = res
        # Size / modifiedTime / version changed; force a re-fetch on next access.
        self._loaded = False

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.path})"

    def file_list_repr(self, use_colors: bool = True) -> str:
        """How the file name should be displayed within a file list."""
        return self.name


from gservices.drive.document_file import DocumentFile
from gservices.drive.folder import Folder
from gservices.drive.root import SharedDrive
from gservices.drive.shortcut import Shortcut

# SpreadsheetFile isn't imported here because the natural import order
# (spreadsheet_file -> file) creates a cycle. Methods that need it import
# locally; see `resolve_from_mime` and `is_spreadsheet`.
