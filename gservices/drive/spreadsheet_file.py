from __future__ import annotations
from gservices.drive.file import File


class SpreadsheetFile(File):
    MIME = "application/vnd.google-apps.spreadsheet"

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[32m{self.name}\033[m"
        return self.name
