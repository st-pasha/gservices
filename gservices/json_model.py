"""Faster JSON deserialization for googleapiclient responses.

`googleapiclient.model.JsonModel` uses stdlib `json.loads` for response
parsing. On large bodies (e.g. snapshotting a spreadsheet with many sheets
via `includeGridData=True`) that's a measurable chunk of wall time — roughly
6–7s per snapshot on a 30-sheet document. `orjson` is a Rust-based drop-in
that parses ~1.6–5× faster depending on response shape and skips the extra
bytes→str decode the stdlib path performs.

Use by passing `model=OrjsonModel()` to `googleapiclient.discovery.build(...)`.
"""

import orjson
from googleapiclient.model import JsonModel  # type: ignore[reportMissingTypeStubs]


class OrjsonModel(JsonModel):
    def deserialize(self, content: bytes | str) -> object:
        # googleapiclient's behavior: an empty body deserializes to itself.
        if not content:
            return content
        if isinstance(content, str):
            content = content.encode("utf-8")
        return orjson.loads(content)
