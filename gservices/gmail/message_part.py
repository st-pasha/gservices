from typing import TYPE_CHECKING
import base64


if TYPE_CHECKING:
    import googleapiclient._apis.gmail.v1.resources as g  # type: ignore


class MessagePart:
    def __init__(self, data: "g.MessagePart", message: "Message"):
        self._data = data
        self._message = message
        self._headers: dict[str, str] = {}
        self._body: bytes | None = None
        self._attachment_id: str | None = None
        for header in data.get("headers", []):
            name = header.get("name", "")
            value = header.get("value", "")
            if name:
                self._headers[name] = value
        if "body" in data:
            body = data["body"]
            if "data" in body:
                self._body = base64.b64decode(body["data"], b"-_")
            if "attachmentId" in body:
                self._attachment_id = body["attachmentId"]

    @property
    def part_id(self) -> str:
        return self._data.get("partId", "")

    @property
    def filename(self) -> str:
        return self._data.get("filename", "")

    @property
    def mime_type(self) -> str:
        return self._data.get("mimeType", "")

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    @property
    def body(self) -> bytes:
        if self._body is None:
            self._body = b""
            if self._attachment_id:
                res = (
                    self._message.resource.users()
                    .messages()
                    .attachments()
                    .get(
                        id=self._attachment_id, userId="me", messageId=self._message.id
                    )
                    .execute()
                )
                self._body = base64.b64decode(res.get("data", ""), b"-_")
        return self._body

    def __repr__(self) -> str:
        parts = [f"MessagePart(id={self.part_id!r}, mime={self.mime_type!r}"]
        if self.filename:
            parts.append(f", file={self.filename!r}")
        parts.append(")")
        return "".join(parts)


from gservices.gmail.message import Message
