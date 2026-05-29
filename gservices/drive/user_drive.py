from typing import TYPE_CHECKING, override

from gservices.drive.folder import Folder

if TYPE_CHECKING:
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore

    from gservices.drive.drive_service import DriveService


class UserDrive(Folder):
    def __init__(self, data: g.File, drive: DriveService):
        super().__init__(data, drive)
        self._shared_drive_id = ""

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[32;1m{self.name}\033[m/"
        return self.name

    @override
    def delete(self, trash: bool = True) -> None:
        raise RuntimeError("The user drive cannot be deleted")
