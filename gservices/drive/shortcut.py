from typing import TYPE_CHECKING
from gservices.drive.file import File
from gservices.drive.path import Path

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore


class Shortcut(File):
    MIME = "application/vnd.google-apps.shortcut"

    def __init__(self, data: "g.File", drive: "DriveService"):
        super().__init__(data, drive)
        self._target: File | None = None
        self._broken: bool = False
        assert data.get("mimeType", Shortcut.MIME) == Shortcut.MIME
        assert "shortcutDetails" in data

    @property
    def mime_type(self) -> str:
        return Shortcut.MIME

    @property
    def target(self) -> File:
        if self._target is None:
            details = self._data.get("shortcutDetails", {})
            target_id = details["targetId"]
            try:
                self._target = self._drive.get(id=target_id)
            except FileNotFoundError:
                self._target = MissingFile(
                    {
                        "id": target_id,
                        "name": self.name,
                        "mimeType": details["targetMimeType"],
                    },
                    self._drive,
                )
                self._broken = True
        return self._target

    @property
    def is_broken(self) -> bool:
        self.target  # Force checking that the target exists
        return self._broken

    def file_list_repr(self, use_colors: bool = True) -> str:
        icon = " \u2718" if self.is_broken else " \u21aa"
        if use_colors:
            res = self.target.file_list_repr() + icon
            if self.target.name != self.name:
                res = res.replace(self.target.name, self.name, 1)
            if self.is_broken:
                res = f"\033[2m{res}\033[m"
            return res
        else:
            return self.name + icon


class MissingFile(File):
    def __init__(self, data: "g.File", drive: "DriveService"):
        super().__init__(data, drive)
        self._path = Path(("?",))
