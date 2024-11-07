from typing import TYPE_CHECKING, Literal, cast

from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError

from gservices.drive.file import File
from gservices.drive.file_list import FileList
from gservices.drive.folder import Folder
from gservices.drive.path import Path
from gservices.drive.root import Root, UserDrive

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore
    from google.oauth2.credentials import Credentials


class DriveService:
    def __init__(self, resource: "g.DriveResource"):
        self._resource = resource
        self._ids: dict[str, File] = {}
        self._paths: dict[Path, File] = {}
        root = Root(self)
        self.cache(root)
        self._current_dir: Path = root.path
        self._user_drive: UserDrive = root.user_drive

    @staticmethod
    def build(credentials: "Credentials") -> "DriveService":
        resource = build("drive", "v3", credentials=credentials)
        return DriveService(resource)

    # ----------------------------------------------------------------------------------
    # Shell-like API
    # ----------------------------------------------------------------------------------

    def ls(self, path: str | Path = "") -> FileList:
        path = self._resolve_path(path)
        file = self.get(path)
        if not isinstance(file, Folder):
            raise NotADirectoryError(f"Path `{path}` is not a directory")
        return file.list()

    def cd(self, path: str | Path) -> None:
        self._current_dir = self._resolve_path(path)

    def pwd(self) -> Path:
        return self._current_dir

    def mkdir(self, path: str | Path) -> None:
        self.mkfile(path, "folder")

    def mkfile(
        self, path: str | Path, kind: Literal["document", "spreadsheet", "folder"]
    ) -> None:
        path = self._resolve_path(path)
        parent = self.get(path.parent)
        if isinstance(parent, Root):
            raise ValueError("Cannot create directories within the Root")
        if not isinstance(parent, Folder):
            raise NotADirectoryError(
                f"The parent path `{parent.path}` is not a directory"
            )
        parent.make_file(path.basename, kind)

    def rm(self, path: str | Path) -> None:
        path = self._resolve_path(path)
        file = self.get(path)
        file.delete()

    def cp(self, source: str | Path, dest: str | Path) -> None:
        """
        Copies file(s) from [source] to [dest].

        The paths can be either normal, or have a trailing slash, which indicates the
        *contents* of a directory instead of the directory itself. The following
        combinations are possible (assuming directories "A" and "B"):

            cp("A/", "B/") - all files in directory A are copied into directory B;
            cp("A/", "B") - not allowed;
            cp("A", "B/") - file/directory A is copied inside directory B;
            cp("A", "B") - file/directory A is copied with the new name B.
        """
        source = self._resolve_path(source)
        dest = self._resolve_path(dest)
        if source.has_tail and not self.get(source).is_dir:
            raise NotADirectoryError(f"Path `{source}` is not a directory")
        if dest.has_tail and not self.get(dest).is_dir:
            raise NotADirectoryError(f"Target path `{dest}` is not a directory")
        source_file = self.get(source)
        if source.has_tail:
            if dest.has_tail:
                for file in cast(Folder, source_file).list():
                    file.copy_to(dest)
            else:
                raise ValueError(
                    "Operation not allowed: the target path must have a trailing /"
                )
        else:
            if dest.has_tail:
                source_file.copy_to(dest / source_file.name)
            else:
                source_file.copy_to(dest)

    def mv(self, source: str | Path, dest: str | Path) -> None:
        source = self._resolve_path(source)
        dest = self._resolve_path(dest)
        if source.has_tail and not self.get(source).is_dir:
            raise NotADirectoryError(f"Path `{source}` is not a directory")
        if dest.has_tail and not self.get(dest).is_dir:
            raise NotADirectoryError(f"Target path `{dest}` is not a directory")
        source_file = self.get(source)
        if source.has_tail:
            if dest.has_tail:
                for file in cast(Folder, source_file).list():
                    file.move_to(dest)
            else:
                raise ValueError(
                    "Operation not allowed: the target path must have a trailing /"
                )
        else:
            if dest.has_tail:
                source_file.move_to(dest)
            else:
                source_file.move_to(dest.parent)
                source_file.rename(dest.basename)

    def get(self, path: str | Path | None = None, id: str | None = None) -> File:
        if id is not None:
            if id not in self._ids:
                self._fetch_file_by_id(id)
            return self._ids[id]
        elif path is not None:
            path = self._resolve_path(path)
            if path not in self._paths:
                parent = self.get(path.parent)
                if not parent.is_dir:
                    raise NotADirectoryError(f"Parent path `{path}` is not a directory")
                cast(Folder, parent).list()
                if path not in self._paths:
                    raise FileNotFoundError(f"File `{path}` does not exist")
            return self._paths[path]
        else:
            raise TypeError("Missing either `path` or `id`")

    def exists(self, path: str | Path | None = None, id: str | None = None) -> bool:
        try:
            self.get(path, id)
            return True
        except FileNotFoundError:
            return False

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def _resolve_path(self, path: str | Path) -> Path:
        if isinstance(path, str):
            return Path.from_string(path, self)
        else:
            return path

    def _fetch_file_by_id(self, file_id: str) -> None:
        try:
            res = (
                self._resource.files()
                .get(fileId=file_id, fields=File.FIELDS, supportsAllDrives=True)
                .execute()
            )
            file = File.resolve_from_mime(res, self)
            self.cache(file)
        except HttpError as e:
            if e.status_code == 404:  # type: ignore
                raise FileNotFoundError(f"File id={file_id} not found")
            raise

    @property
    def resource(self) -> "g.DriveResource":
        return self._resource

    @property
    def user_drive(self) -> UserDrive:
        return self._user_drive

    def cache(self, file: File) -> None:
        self._ids[file.id] = file
        self._paths[file.path] = file

    def uncache(self, file: File) -> None:
        del self._ids[file.id]
        del self._paths[file.path]
