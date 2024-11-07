from typing import TYPE_CHECKING

from gservices.drive.folder import Folder
from gservices.drive.path import Path

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService


class Root(Folder):
    def __init__(self, drive: "DriveService"):
        super().__init__({"id": "", "name": ""}, drive)
        self._path = Path(("",))
        self._shared_drive_id = ""

    @property
    def parent(self) -> "Folder":
        raise ValueError("Root folder doesn't have a parent")

    @property
    def user_drive(self) -> "UserDrive":
        ud = self.list()[0]
        assert isinstance(ud, UserDrive)
        return ud

    def _fetch_files(self) -> "FileList":
        out = FileList([], path=self.path)

        # User drive
        res = self._drive.resource.files().get(fileId="root").execute()
        user_drive = UserDrive(res, self._drive)
        self._drive.cache(user_drive)
        out.append(user_drive)

        # Shared drives
        res = self._drive.resource.drives().list().execute()
        for item in res.get("drives", []):
            shared_drive = SharedDrive(item, self._drive)
            self._drive.cache(shared_drive)
            out.append(shared_drive)

        return out


from gservices.drive.file_list import FileList
from gservices.drive.shared_drive import SharedDrive
from gservices.drive.user_drive import UserDrive
