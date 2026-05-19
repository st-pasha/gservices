from collections.abc import Sequence

from google.oauth2.credentials import Credentials

LOCALHOST: str
DEFAULT_PORTS_TO_TRY: int

def is_port_open(port: int) -> bool: ...
def find_open_port(start: int = ..., stop: int | None = ...) -> None: ...
def get_user_credentials(
    scopes: Sequence[str],
    client_id: str,
    client_secret: str,
    minimum_port: int = ...,
    maximum_port: int | None = ...,
) -> Credentials: ...
