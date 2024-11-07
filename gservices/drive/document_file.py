from gservices.drive.file import File


class DocumentFile(File):
    MIME = "application/vnd.google-apps.document"

    def file_list_repr(self, use_colors: bool = True) -> str:
        if use_colors:
            return f"\033[36m{self.name}\033[m"
        return self.name
