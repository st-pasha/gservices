from __future__ import annotations
import json
from typing import Any, Protocol, Sequence, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gservices.drive.drive_service import DriveService
from gservices.gmail.gmail_service import GmailService
from gservices.sheets.sheets_service import SheetsService
from gservices.oauth2_scopes import OAuth2Scope


class GoogleService:
    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._drive_service: DriveService | None = None
        self._gmail_service: GmailService | None = None
        self._sheets_service: SheetsService | None = None

    @staticmethod
    def connect(
        token: dict[str, str],
        credentials: dict[str, Any] | None = None,
        scopes: Sequence[OAuth2Scope] | None = None,
        log: _Logger | None = None,
    ) -> GoogleService:
        """
        Initialize Google API Service, using an existing [token] or obtaining
        the token data from [credentials].

        The [token] dictionary should contain the session token data previously
        issued by Google API. It can also be an empty dictionary if no token
        was obtained yet. If needed, this method will obtain a new token or
        refresh the provided token, updating the [token] dictionary in-place.
        The user is advised to store the token data for future use.

        The [scopes] is a list of authorization scopes requested by the
        application. This can be None if using an existing token, but must be
        explicitly provided when requesting a new token. If the list of
        requested [scopes] is wider than the one used to obtain the previous
        token, then the access will need to be re-authorized, and the token will
        be refreshed.

        The [credentials] dictionary contains application's credentials for
        accessing Google API. This file can be obtained from Google Developer
        Console, it will contain the application id and a secret key. The
        credentials data will be used to request an access token; consequently
        this parameter may be omitted if a valid [token] is supplied.
        """
        creds: Credentials | None = None

        # Create Credentials object from [token]
        if token:
            assert token.get("scopes")
            granted_scopes = cast(list[OAuth2Scope], token["scopes"])
            if scopes is None:
                scopes = granted_scopes
            extra_scopes = set(scopes) - set(granted_scopes)
            if extra_scopes:
                if log:
                    log.info(
                        "Additional scopes requested that are not available in the"
                        f" token file: {list(extra_scopes)}. Access will need to be"
                        " re-requested."
                    )
            else:
                creds = Credentials.from_authorized_user_info(token, scopes)

        # Create a new Credentials object, or refresh the existing one
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                if log:
                    log.info("The token has expired, refreshing...")
                creds.refresh(Request())
            else:
                if not (scopes and credentials):
                    raise ValueError(
                        "The list of scopes and credentials must be provided explicitly"
                        " when requesting a new token"
                    )
                if log:
                    log.info("Requesting a new access token from the user")
                flow = InstalledAppFlow.from_client_config(credentials, scopes)
                creds = flow.run_local_server(port=0)
            if log:
                log.info(f"New token expiry is {creds.expiry}")
            token.clear()
            token.update(json.loads(creds.to_json()))

        return GoogleService(creds)

    @property
    def Drive(self) -> DriveService:
        if self._drive_service is None:
            self._drive_service = DriveService.build(self._credentials)
        return self._drive_service

    @property
    def Gmail(self) -> GmailService:
        if self._gmail_service is None:
            self._gmail_service = GmailService.build(self._credentials)
        return self._gmail_service

    @property
    def Sheets(self) -> SheetsService:
        if self._sheets_service is None:
            self._sheets_service = SheetsService.build(self._credentials)
        return self._sheets_service


class _Logger(Protocol):
    def info(self, msg: str, /): ...
    def warning(self, msg: str, /): ...
