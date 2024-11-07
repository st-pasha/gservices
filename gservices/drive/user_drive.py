from typing import TYPE_CHECKING
from gservices.drive.folder import Folder

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore


class UserDrive(Folder):
    def __init__(self, data: "g.File", drive: "DriveService"):
        super().__init__(data, drive)
        self._shared_drive_id = ""

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[32;1m{self.name}\033[m/"
        return self.name

    def remove(self, trash: bool = True) -> None:
        raise NotImplementedError("A user drive cannot be removed")
