from googleapiclient.http import BatchHttpRequest

from gservices.gmail.thread import Thread


class ThreadList(list[Thread]):
    def __repr__(self) -> str:
        self._load_all()
        items = [item.email_list_repr() for item in self]
        return "\n".join(items)

    def _load_all(self):
        if len(self) > 0:
            batch = BatchHttpRequest()
            for item in self:
                _request, _callback = item.batch_load()
                # batch.add(_request, callback=_callback)  # type: ignore
            batch.execute()
