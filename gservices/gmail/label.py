from __future__ import annotations
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import googleapiclient._apis.gmail.v1.resources as g  # type: ignore


class Label:
    def __init__(self, data: "g.Label", gmail: GmailService):
        self._data = data
        self._service = gmail

    @property
    def id(self) -> str:
        return self._data.get("id", "?")

    @property
    def name(self) -> str:
        return self._data.get("name", "?")

    @property
    def type(self) -> Literal["system", "user"]:
        return self._data.get("type", "system")

    @property
    def n_messages_total(self) -> int:
        if "messagesTotal" not in self._data:
            self._load()
        return self._data.get("messagesTotal", 0)

    @property
    def n_messages_unread(self) -> int:
        if "messagesUnread" not in self._data:
            self._load()
        return self._data.get("messagesUnread", 0)

    @property
    def n_threads_total(self) -> int:
        if "threadsTotal" not in self._data:
            self._load()
        return self._data.get("threadsTotal", 0)

    @property
    def n_threads_unread(self) -> int:
        if "threadsUnread" not in self._data:
            self._load()
        return self._data.get("threadsUnread", 0)

    @property
    def message_list_visibility(self) -> Literal["show", "hide"]:
        return self._data.get("messageListVisibility", "hide")

    @property
    def label_list_visibility(self) -> Literal["show", "showIfUnread", "hide"]:
        status = self._data.get("labelListVisibility")
        if status == "labelShow":
            return "show"
        elif status == "labelShowIfUnread":
            return "showIfUnread"
        else:
            return "hide"

    @property
    def bg_color(self) -> str | None:
        return self._data.get("color", {}).get("backgroundColor")

    @property
    def fg_color(self) -> str | None:
        return self._data.get("color", {}).get("textColor")

    def _load(self):
        self._data = (
            self._service.resource.users()
            .labels()
            .get(id=self.id, userId="me")
            .execute()
        )

    def __repr__(self) -> str:
        return f"Label({self.name})"


from gservices.gmail.gmail_service import GmailService
