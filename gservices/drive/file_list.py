from gservices.drive.file import File
from gservices.drive.path import Path


class FileList(list[File]):
    USE_COLORS: bool = True

    def __init__(self, data: list[File], path: Path):
        super().__init__(data)
        self._path = path

    def __repr__(self) -> str:
        dirs: list[str] = []
        files: list[str] = []
        if self.USE_COLORS:
            dirs.append(f"\033[1m{self._path}/\033[m")
        else:
            dirs.append(str(self._path))
        for item in self:
            item_repr = "  " + item.file_list_repr(self.USE_COLORS)
            if item.is_dir:
                dirs.append(item_repr)
            else:
                files.append(item_repr)
        dirs.extend(files)
        return "\n".join(dirs)
