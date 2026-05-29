import re
from typing import TYPE_CHECKING, Literal, cast

from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError

from gservices.drive.file import File
from gservices.drive.file_list import FileList
from gservices.drive.folder import Folder
from gservices.drive.path import Path
from gservices.drive.root import Root, UserDrive
from gservices.json_model import OrjsonModel

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore
    from google.auth.credentials import Credentials


class DriveService:
    def __init__(self, resource: g.DriveResource):
        self._resource = resource
        self._ids: dict[str, File] = {}
        # A path may correspond to multiple files: Drive permits duplicate
        # names within a folder. Lookups on an ambiguous path raise; callers
        # must use `id=` to disambiguate.
        self._paths: dict[Path, list[File]] = {}
        root = Root(self)
        self.cache(root)
        self._current_dir: Path = root.path
        self._user_drive: UserDrive = root.user_drive

    @staticmethod
    def build(credentials: Credentials) -> DriveService:
        resource = build(
            "drive", "v3", credentials=credentials, model=OrjsonModel()
        )
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
        self,
        path: str | Path,
        kind: Literal["document", "spreadsheet", "folder", "slides", "drawing"],
    ) -> None:
        path = self._resolve_path(path)
        parent = self.get(path.parent)
        if isinstance(parent, Root):
            raise ValueError("Cannot create directories within the Root")  # noqa: TRY004
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
                    file.copy_to(dest / file.name)
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
            bucket = self._paths[path]
            if len(bucket) > 1:
                ids = ", ".join(repr(f.id) for f in bucket)
                raise ValueError(
                    f"Path `{path}` is ambiguous: matches {len(bucket)} files "
                    f"(ids: {ids}). Look one up with get(id=...)."
                )
            return bucket[0]
        else:
            raise TypeError("Missing either `path` or `id`")

    def exists(self, path: str | Path | None = None, id: str | None = None) -> bool:
        try:
            self.get(path, id)
            return True
        except FileNotFoundError:
            return False

    def find(self, path: str | Path, mime_type: str | None = None) -> list[File]:
        """
        Returns a list of files matching the pattern [path], optionally
        restricted to those with the requested [mime_type].

        Each path segment is matched against the children of the previous
        segment's result(s):

        1. If a child's name matches the segment **exactly**, that child is
           taken (regardless of any regex metacharacters the segment contains).
        2. Otherwise the segment is compiled as a regex and the children whose
           names fully match it are taken. The match is anchored at both ends
           (`re.fullmatch`), so `foo` does not match `foobar`.
        3. The special segments `*` and `**` match any single child and any
           descendant respectively.

        If neither exact match nor regex matches anything for some segment,
        the search prunes that branch and continues with the others.
        """
        path = self._resolve_path(path)
        files = self._find_paths(path)
        if mime_type:
            return [file for file in files if file.mime_type == mime_type]
        else:
            return files

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
    def resource(self) -> g.DriveResource:
        return self._resource

    @property
    def user_drive(self) -> UserDrive:
        return self._user_drive

    def cache(self, file: File) -> None:
        self._ids[file.id] = file
        bucket = self._paths.setdefault(file.path, [])
        # Compare by id, not identity — a re-fetch may have produced a new
        # File instance for an already-cached id.
        if not any(f.id == file.id for f in bucket):
            bucket.append(file)

    def uncache(self, file: File) -> None:
        self._ids.pop(file.id, None)
        self._uncache_path(file)

    def _uncache_path(self, file: File) -> None:
        """Internal: drop the file's `_paths` entry, leaving `_ids` intact.

        Used when a file's path becomes stale (parent moved/renamed) but the
        file itself still exists and is reachable by id.
        """
        bucket = self._paths.get(file.path)
        if bucket is None:
            return
        remaining = [f for f in bucket if f.id != file.id]
        if remaining:
            self._paths[file.path] = remaining
        else:
            del self._paths[file.path]

    def _find_paths(self, path: Path) -> list[File]:
        if path in self._paths:
            return list(self._paths[path])
        else:
            parents = self._find_paths(path.parent)
            out: list[File] = []
            for parent in parents:
                if isinstance(parent, Folder):
                    children = self._find_files_in_folder(parent, path.basename)
                    out.extend(children)
            return out

    def _find_files_in_folder(self, folder: Folder, pattern: str) -> list[File]:
        if pattern == "*":
            return list(folder.list())
        if pattern == "**":
            out: list[File] = []
            self._find_all_files_recursively(folder, out)
            return out
        children = folder.list()
        # 1. Exact name match wins over regex — so literal names like
        # "Report (Q1).csv" don't get mis-interpreted as regex.
        exact = [f for f in children if f.name == pattern]
        if exact:
            return exact
        # 2. Fall back to regex, anchored at both ends.
        try:
            regex = re.compile(pattern)
        except re.error:
            return []
        return [f for f in children if regex.fullmatch(f.name)]

    def _find_all_files_recursively(self, folder: Folder, out: list[File]):
        for file in folder.list():
            out.append(file)
            if isinstance(file, Folder):
                self._find_all_files_recursively(file, out)
