from __future__ import annotations
from typing import TYPE_CHECKING, Generator, Sequence

from googleapiclient.discovery import build  # type: ignore

if TYPE_CHECKING:
    import googleapiclient._apis.gmail.v1.resources as g  # type: ignore
    from google.oauth2.credentials import Credentials


class GmailService:
    def __init__(self, resource: "g.GmailResource"):
        self._resource = resource
        self._labels: list[Label] | None = None
        self._label_map: dict[str, Label] = {}
        self._read_page_token: str = ""
        self._thread_cache: dict[str, Thread] = {}

    @staticmethod
    def build(credentials: "Credentials") -> GmailService:
        resource = build("gmail", "v1", credentials=credentials)
        return GmailService(resource)

    # ----------------------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------------------

    def read(
        self, query: str = "", labels: Sequence[Label] = ()
    ) -> Generator[Thread, None, None]:
        """
        Reads a list of threads that match the [query] or have one of the [labels]
        applied, and returns them as an iterator.
        """
        page_token = ""
        while True:
            result = (
                self._resource.users()
                .threads()
                .list(
                    userId="me",
                    pageToken=page_token,
                    q=query,
                    labelIds=[label.id for label in labels],
                    maxResults=20 if query or labels else 50,
                )
                .execute()
            )
            for record in result.get("threads", []):
                thread_id = record.get("id", "")
                history_id = record.get("historyId", "")
                existing_thread = self._thread_cache.get(thread_id)
                if existing_thread and existing_thread.history_id == history_id:
                    yield existing_thread
                else:
                    thread = Thread(record, self)
                    self._thread_cache[thread_id] = thread
                    yield thread
            page_token = result.get("nextPageToken", "")
            if not page_token:
                break

    def get_thread(self, id: str) -> Thread:
        return self._thread_cache[id]

    @property
    def labels(self) -> list[Label]:
        if self._labels is None:
            self._load_labels()
            assert self._labels is not None
        return self._labels

    def get_label(self, name: str) -> Label | None:
        if self._labels is None:
            self._load_labels()
        return self._label_map.get(name)

    # ----------------------------------------------------------------------------------
    # Private
    # ----------------------------------------------------------------------------------

    def _load_labels(self):
        data = self.resource.users().labels().list(userId="me").execute()
        self._labels = []
        self._label_map = {}
        for record in data.get("labels", []):
            label = Label(record, self)
            self._labels.append(label)
            self._label_map[label.id] = label
            self._label_map[label.name] = label

    @property
    def resource(self) -> "g.GmailResource":
        return self._resource


from gservices.gmail.label import Label
from gservices.gmail.thread import Thread
