"""Minimal stub for `googleapiclient.model.JsonModel` — covers just the
surface we subclass in `gservices.json_model.OrjsonModel`.

The upstream package ships no type stubs, so without this file pyright
emits `reportMissingTypeStubs` for the import.
"""

from typing import Any

class JsonModel:
    accept: str
    content_type: str
    alt_param: str

    def __init__(self, data_wrapper: bool = False) -> None: ...
    def serialize(self, body_value: object) -> str: ...
    def deserialize(self, content: bytes | str) -> Any: ...
