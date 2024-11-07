import re
from typing import TYPE_CHECKING, Iterator
import warnings

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    import googleapiclient._apis.gmail.v1.resources as g  # type: ignore


class Message:
    def __init__(self, data: "g.Message", gmail: "GmailService"):
        self._data = data
        self._gmail = gmail
        self._headers: dict[str, str] = {}
        self._text: str = ""
        self._html: str = ""
        self._attachments: list[MessagePart] = []
        self._process_payload(data.get("payload", {}))

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def thread_id(self) -> str:
        return self._data.get("threadId", "")

    @property
    def timestamp(self) -> int:
        return int(self._data.get("internalDate", "0"))

    @property
    def subject(self) -> str:
        return self._headers.get("Subject", "")

    @property
    def from_(self) -> str:
        return self._headers.get("From", "")

    @property
    def to_(self) -> str:
        return self._headers.get("To", "")

    @property
    def text(self) -> str:
        return self._text

    @property
    def html(self) -> str:
        return self._html

    @property
    def labels(self) -> list[str]:
        return self._data.get("labelIds", [])

    @property
    def attachments(self) -> list["MessagePart"]:
        return self._attachments

    def email_list_repr(self, full: bool = True) -> str:
        if full:
            lines: list[str] = []
            lines.append(f"[{self.id}] ")
            lines.append(f"  Subject: {self.subject}")
            lines.append(f"  From:    {self.from_}")
            lines.append(f"  To:      {self.to_}")
            if self._attachments:
                filenames = ", ".join(a.filename for a in self._attachments)
                lines.append(f"  Attach:  {filenames}")
            lines.append("")
            if self._text:
                for text_line in self._text.splitlines():
                    lines.append("  " + text_line)
            return "\n".join(lines)
        else:
            return f"[{self.id}] {self.subject}"

    def _process_payload(self, payload: "g.MessagePart"):
        for part in self._process_part(payload):
            if not self._headers:
                self._headers = part.headers
            if part.filename == "":
                mime = part.mime_type
                if mime == "text/plain":
                    self._text = part.body.decode("utf-8")
                elif mime == "text/html":
                    self._html = part.body.decode("utf-8")
                elif mime in ("multipart/mixed", "multipart/alternative"):
                    pass
                else:
                    warnings.warn(f"Unknown mime type: {mime}")
            else:
                self._attachments.append(part)
        if self._html and not self._text:
            soup = BeautifulSoup(self._html, "html.parser")
            self._text = re.sub(r"\n{3,}", "\n\n", soup.get_text().strip())

    def _process_part(self, data: "g.MessagePart") -> Iterator["MessagePart"]:
        yield MessagePart(data, self)
        for part in data.get("parts", []):
            yield from self._process_part(part)

    def __repr__(self) -> str:
        return self.email_list_repr()

    @property
    def resource(self) -> "g.GmailResource":
        return self._gmail.resource


from gservices.gmail.gmail_service import GmailService
from gservices.gmail.message_part import MessagePart
