from __future__ import annotations
from typing import TYPE_CHECKING, cast

from gservices.drive.folder import Folder

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService
    import googleapiclient._apis.drive.v3.resources as g  # type: ignore


class SharedDrive(Folder):
    def __init__(self, data: g.Drive, drive: DriveService):
        super().__init__(cast("g.File", data), drive)
        self._shared_drive_id = self.id

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[36;1m{self.name}\033[m/"
        return self.name

    def remove(self, trash: bool = True) -> None:
        raise NotImplementedError("A shared drive cannot be removed")
