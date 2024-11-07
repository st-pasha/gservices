from typing import TYPE_CHECKING, cast

from gservices.drive.path import Path

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore

    from gservices.drive.drive_service import DriveService


class File:
    FIELDS = "id,name,mimeType,parents,shortcutDetails,contentRestrictions"

    def __init__(self, data: "g.File", drive: "DriveService"):
        self._data = data
        self._drive = drive
        self._path: Path | None = None
        self._parent: Folder | None = None
        self._shared_drive_id: str | None = None

    @staticmethod
    def resolve_from_mime(data: "g.File", service: "DriveService") -> "File":
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
    def parent(self) -> "Folder":
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
            self._shared_drive_id = self.parent.shared_drive_id
        return self._shared_drive_id

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
        self._data = res
        self._drive.cache(self)

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

    def copy_to(self, dest: Path) -> None:
        dest_dir = self._drive.get(dest.parent)
        if not isinstance(dest_dir, Folder):
            raise NotADirectoryError(
                f"Destination path `{dest_dir.path}` is not a directory"
            )
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
        self._drive.uncache(self)
        self.parent.handle_file_removed(self)

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
from gservices.drive.spreadsheet_file import SpreadsheetFile
