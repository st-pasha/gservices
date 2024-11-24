from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gservices.drive.drive_service import DriveService


class Path:
    SEPARATOR = "/"

    def __init__(self, parts: tuple[str, ...], has_tail: bool = False):
        assert len(parts) > 0 and parts[0] in ("", "?")
        self._parts = parts
        self._has_tail = has_tail

    @staticmethod
    def from_string(path: str, drive: DriveService) -> Path:
        if path == "~":
            return drive.user_drive.path
        if path == "." or path == "":
            return drive.pwd()
        parts = path.split(Path.SEPARATOR)
        assert len(parts) > 0
        has_tail = False
        if parts[0] == "~":
            parts = [*drive.user_drive.path._parts, *parts]
        if parts[0] != "":
            parts = [*drive.pwd()._parts, *parts]
        if parts[-1] == "":
            parts.pop()
            has_tail = True
        if ".." in parts or "." in parts:
            new_parts: list[str] = []
            for part in parts:
                if part == ".":
                    continue
                if part == "..":
                    if len(new_parts) <= 1:
                        raise ValueError(
                            "Relative path with `..` cannot go beyond the Root"
                        )
                    new_parts.pop()
                else:
                    new_parts.append(part)
            parts = new_parts
        return Path(tuple(parts), has_tail=has_tail)

    @property
    def is_root(self) -> bool:
        return len(self._parts) == 1

    @property
    def parent(self) -> Path:
        return Path(self._parts[:-1])

    @property
    def basename(self) -> str:
        return self._parts[-1]

    @property
    def has_tail(self) -> bool:
        return self._has_tail

    def __truediv__(self, other: str) -> Path:
        if other == "..":
            return self.parent
        return Path(tuple([*self._parts, other]))

    def __str__(self) -> str:
        if self.is_root:
            return Path.SEPARATOR
        else:
            return Path.SEPARATOR.join(self._parts)

    def __repr__(self) -> str:
        return f"Path({self})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Path)
            and self._parts == other._parts
            and self._has_tail == other._has_tail
        )

    def __hash__(self) -> int:
        return hash(self._parts)
