"""Jellyfin-compatible sign-in, user and server-identity routes.

Clean-room: shapes follow the property names of Jellyfin's published OpenAPI spec only.
Register before jellyfin.register(app): its /jellyfin/{rest:path} catch-all must come last.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import DeviceToken, User
from app.security import (
    APP_PASSWORD_REQUIRED_DETAIL, MAX_PASSWORD_LENGTH, AppPasswordRequired, audit_log, authenticate_rate_limited, client_ip,
)
from app.services import public_address
from app.services.connected_apps import (
    jellyfin_server_id, jellyfin_user, mint_device_token, parse_client_auth, require_jellyfin_enabled, revoke_connected_app,
)
from app.services.media_titles import jellyfin_id

PREFIX = "/jellyfin"  # the private mount; clients reach these routes at the root (routers/jellyfin.py)
JELLYFIN_PRODUCT_NAME = "Jellyfin Server"  # never advertise Emby
# One constant: try "12.1.0" during the manual Infuse session and keep whichever passes.
JELLYFIN_VERSION = "10.11.0"
JELLYFIN_SERVER_NAME = "Lumina"


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def user_dto(user: User, server_id: str) -> dict[str, Any]:
    return {
        "Name": user.username,
        "ServerId": server_id,
        "ServerName": JELLYFIN_SERVER_NAME,
        "Id": jellyfin_id(user.id),
        "HasPassword": True,
        "HasConfiguredPassword": True,
        "HasConfiguredEasyPassword": False,
        "EnableAutoLogin": False,
        "Configuration": {
            "PlayDefaultAudioTrack": True,
            "SubtitleMode": "Default",
            "DisplayMissingEpisodes": False,
            "GroupedFolders": [],
            "OrderedViews": [],
            "LatestItemsExcludes": [],
            "MyMediaExcludes": [],
            "HidePlayedInLatest": True,
            "RememberAudioSelections": True,
            "RememberSubtitleSelections": True,
            "EnableNextEpisodeAutoPlay": True,
            "DisplayCollectionsView": False,
            "EnableLocalPassword": False,
        },
        "Policy": {
            "IsAdministrator": False,  # always: no admin surface over this API
            "IsHidden": False,
            "IsDisabled": False,
            "EnableAllFolders": True,
            "EnabledFolders": [],
            "EnableAllChannels": True,
            "EnabledChannels": [],
            "EnableAllDevices": True,
            "EnabledDevices": [],
            "BlockedTags": [],
            "AllowedTags": [],
            "EnableUserPreferenceAccess": True,
            "EnableMediaPlayback": True,
            "EnableAudioPlaybackTranscoding": True,
            "EnableVideoPlaybackTranscoding": True,
            "EnablePlaybackRemuxing": True,
            # Required (non-null) in Jellyfin SDK 1.7 UserPolicy: Jellyfin for Android TV 0.19.10 cannot sign in without them.
            "EnableSyncTranscoding": True,
            "EnableMediaConversion": True,
            "EnableContentDownloading": True,
            "EnableRemoteAccess": True,
            "EnableContentDeletion": False,
            "EnableContentDeletionFromFolders": [],
            "EnableCollectionManagement": False,
            "EnableLyricManagement": False,
            "EnableSubtitleManagement": False,
            "EnableLiveTvAccess": False,
            "EnableLiveTvManagement": False,
            "EnableRemoteControlOfOtherUsers": False,
            "EnableSharedDeviceControl": False,
            "EnablePublicSharing": False,
            "ForceRemoteSourceTranscoding": False,
            "InvalidLoginAttemptCount": 0,
            "LoginAttemptsBeforeLockout": -1,
            "MaxActiveSessions": 0,
            "RemoteClientBitrateLimit": 0,
            "SyncPlayAccess": "None",
            "AuthenticationProviderId": "Lumina",
            "PasswordResetProviderId": "Lumina",
        },
    }


def session_info(record: DeviceToken, user: User, server_id: str) -> dict[str, Any]:
    info = {
        "Id": jellyfin_id(record.id),
        "UserId": jellyfin_id(user.id),
        "UserName": user.username,
        "Client": record.client,
        "DeviceId": record.device_id,
        "DeviceName": record.device_name,
        "ApplicationVersion": record.client_version,
        "LastActivityDate": _iso(record.last_seen_at),
        "LastPlaybackCheckIn": _iso(record.last_seen_at) or "0001-01-01T00:00:00Z",  # required by SDK 1.7, like HasCustomDeviceName
        "HasCustomDeviceName": False,
        "PlayState": {"CanSeek": False, "IsPaused": False, "IsMuted": False, "RepeatMode": "RepeatNone", "PlaybackOrder": "Default"},
        "IsActive": True,
        "SupportsMediaControl": False,
        "SupportsRemoteControl": False,
        "PlayableMediaTypes": [],
        "AdditionalUsers": [],
        "SupportedCommands": [],
        "ServerId": server_id,
    }
    return {key: value for key, value in info.items() if value is not None}


def _local_address(request: Request) -> str:
    """The address the caller already used: a request through the public (or local) address never learns another one."""
    return public_address.origin_for(request.url.hostname) or settings.resolved_app_public_url


def _server_port(request: Request) -> int:
    """The port the caller reached the server on: the configured address it arrived by (the proxy hides it from request.url)."""
    arrived = urlparse(_local_address(request))
    return arrived.port or (443 if arrived.scheme == "https" else 80 if arrived.scheme == "http" else request.url.port or 80)


def _public_system_info(request: Request, db: Session) -> dict[str, Any]:
    return {
        "LocalAddress": _local_address(request),  # the API is also served at the root
        "ServerName": JELLYFIN_SERVER_NAME,
        "Version": JELLYFIN_VERSION,
        "ProductName": JELLYFIN_PRODUCT_NAME,
        "Id": jellyfin_server_id(db),
        "StartupWizardCompleted": True,
        "OperatingSystem": "Linux",
    }


def get_public_system_info(request: Request, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return _public_system_info(request, db)


def get_system_info(request: Request, _user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    # No *Path fields: the server's filesystem layout is never disclosed.
    return {
        **_public_system_info(request, db),
        "WebSocketPortNumber": _server_port(request),
        "HasPendingRestart": False,
        "IsShuttingDown": False,
        "SupportsLibraryMonitor": False,
        "CanSelfRestart": False,
        "CanLaunchWebBrowser": False,
        "HasUpdateAvailable": False,
        "CompletedInstallations": [],
        "CastReceiverApplications": [],
    }


def ping() -> str:
    return JELLYFIN_PRODUCT_NAME


def list_public_users() -> list[Any]:
    return []  # no user picker: members type their username


def quick_connect_enabled() -> bool:
    return False


def authenticate_by_name(
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    fields = {str(key).lower(): value for key, value in payload.items()}
    username, password = fields.get("username"), fields.get("pw")
    if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or len(username) > 80 or len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Username and Pw are required.")
    auth = parse_client_auth(request)
    if not auth.device_id:  # checked before the password so a malformed client costs no PBKDF2 work
        raise HTTPException(status_code=400, detail="The client did not send a DeviceId.")
    try:
        user = authenticate_rate_limited(db, request, username, password, app_passwords=True)
    except AppPasswordRequired:
        # A two-step account: the app cannot ask for a code, so it signs in with an app password (Settings → Connected apps).
        raise HTTPException(status_code=401, detail=APP_PASSWORD_REQUIRED_DETAIL) from None
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    try:
        record, token = mint_device_token(
            db, user, auth, last_ip=client_ip(request), app_password_id=getattr(request.state, "app_password_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_log.info("login.success user=%s client=%s app=jellyfin token_id=%s", user.id, client_ip(request), record.id)
    server_id = jellyfin_server_id(db)
    response.headers["Cache-Control"] = "no-store"
    return {"User": user_dto(user, server_id), "SessionInfo": session_info(record, user, server_id), "AccessToken": token, "ServerId": server_id}


def sign_out(request: Request, user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> Response:
    """POST /Sessions/Logout: the client's sign-out revokes its own Connected app (and its streams) at once."""
    try:
        revoke_connected_app(db, user, request.state.connected_app_id)
    except LookupError:
        pass  # a concurrent revoke already removed it
    return Response(status_code=204)


def get_me(user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return user_dto(user, jellyfin_server_id(db))


def get_user(uid: str, user: User = Depends(jellyfin_user), db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return user_dto(user, jellyfin_server_id(db))  # jellyfin_user already 404s any uid but the caller's


def register(app: FastAPI) -> None:
    gate = [Depends(require_jellyfin_enabled)]
    app.get(PREFIX + "/system/info/public", dependencies=gate)(get_public_system_info)
    app.get(PREFIX + "/system/info")(get_system_info)
    app.api_route(PREFIX + "/system/ping", methods=["GET", "POST"], dependencies=gate)(ping)
    app.get(PREFIX + "/users/public", dependencies=gate)(list_public_users)
    app.get(PREFIX + "/quickconnect/enabled", dependencies=gate)(quick_connect_enabled)
    app.post(PREFIX + "/users/authenticatebyname", dependencies=gate)(authenticate_by_name)
    app.post(PREFIX + "/sessions/logout", status_code=204)(sign_out)
    app.get(PREFIX + "/users/me")(get_me)  # before /users/{uid}
    app.get(PREFIX + "/users/{uid}")(get_user)
