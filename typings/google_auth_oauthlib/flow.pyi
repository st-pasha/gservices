from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, Type, TypeVar
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from requests_oauthlib import OAuth2Session

class Flow:
    client_type: Literal["web", "installed"]
    client_config: Mapping[str, Any]
    oauth2session: OAuth2Session
    code_verifier: str
    autogenerate_code_verifier: bool

    def __init__(
        self,
        oauth2session: OAuth2Session,
        client_type: Literal["web", "installed"],
        client_config: Mapping[str, Any],
        redirect_uri: str | None = None,
        code_verifier: str | None = None,
        autogenerate_code_verifier: bool = ...,
    ) -> None: ...
    @classmethod
    def from_client_config(
        cls: Type[_T],
        client_config: Mapping[str, Any],
        scopes: Sequence[str],
        **kwargs: Any,
    ) -> _T: ...
    @classmethod
    def from_client_secrets_file(
        cls: Type[_T],
        client_secrets_file: str | Path,
        scopes: Sequence[str],
        **kwargs: Any,
    ) -> _T: ...
    @property
    def redirect_uri(self) -> str | None: ...
    @redirect_uri.setter
    def redirect_uri(self, value: str | None) -> None: ...
    def authorization_url(self, **kwargs: Any) -> str: ...
    def fetch_token(self, **kwargs: Any) -> dict[str, str]: ...
    @property
    def credentials(self) -> Credentials: ...
    def authorized_session(self) -> AuthorizedSession: ...

class InstalledAppFlow(Flow):
    def run_local_server(
        self,
        host: str = "localhost",
        bind_addr: str | None = None,
        port: int = 8080,
        authorization_prompt_message: str = ...,
        success_message: str = ...,
        open_browser: bool = True,
        redirect_uri_trailing_slash: bool = True,
        timeout_seconds: int | None = None,
        token_audience: str | None = None,
        browser: str | None = None,
        **kwargs: Any,
    ) -> Credentials: ...

_T = TypeVar("_T", bound="Flow")
