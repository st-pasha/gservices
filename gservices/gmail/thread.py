from typing import TYPE_CHECKING
import html
import re
from googleapiclient.errors import HttpError

if TYPE_CHECKING:
    import googleapiclient._apis.gmail.v1.resources as g  # type: ignore


class Thread:
    def __init__(self, data: "g.Thread", gmail: "GmailService"):
        self._data = data
        self._gmail = gmail
        self._messages: list[Message] | None = None
        self._clean_snippet()

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def history_id(self) -> str:
        return self._data.get("historyId", "")

    @property
    def snippet(self) -> str:
        return self._data.get("snippet", "")

    @property
    def messages(self) -> list["Message"]:
        if self._messages is None:
            self.load()
            assert self._messages is not None
        return self._messages

    def load(self) -> None:
        res = self._load_request().execute()
        self._process_load_response("", res, None)

    def batch_load(self):
        return self._load_request(), self._process_load_response

    def _load_request(self) -> "g.ThreadHttpRequest":
        return (
            self._gmail.resource.users()
            .threads()
            .get(id=self.id, userId="me", format="full")
        )

    def _process_load_response(
        self, id: str, data: "g.Thread", exception: HttpError | None
    ) -> None:
        if exception:
            raise exception
        self._data["historyId"] = data.get("historyId", "")
        self._messages = [Message(m, self._gmail) for m in data.get("messages", [])]

    def _clean_snippet(self):
        text = self._data.get("snippet", "")
        text = html.unescape(text)
        text = re.sub("[\u200c\u200d\ufeff]", "", text)
        text = text.strip("\0\t\n\u0020\u00a0\u034f")
        self._data["snippet"] = text

    def __repr__(self) -> str:
        return f"Thread({self.id}, {self.snippet!r})"

    def email_list_repr(self) -> str:
        lines: list[str] = []
        lines.append(f"[{self.id}] @{self.history_id} {self.snippet}")
        for msg in self.messages:
            lines.append("  " + msg.email_list_repr(full=False))
        return "\n".join(lines)


from gservices.gmail.gmail_service import GmailService
from gservices.gmail.message import Message
