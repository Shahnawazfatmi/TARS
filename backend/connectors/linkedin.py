"""
LinkedIn, through the official API only (OAuth 2.0 + REST).

What this supports, with the permissions LinkedIn grants to a normal
self-serve developer app:

    Product "Sign In with LinkedIn using OpenID Connect"
        openid, profile, email  -> your name, picture, email (userinfo)
    Product "Share on LinkedIn"
        w_member_social         -> create posts (text, or text + one image)
                                   on YOUR personal profile

Not supported (needs other products that LinkedIn must approve):
posting as a company page, reading your feed or other people's posts,
post analytics, messages.

Endpoints (LinkedIn docs, Posts API version 202609):
    GET  https://www.linkedin.com/oauth/v2/authorization
    POST https://www.linkedin.com/oauth/v2/accessToken
    GET  https://api.linkedin.com/v2/userinfo
    POST https://api.linkedin.com/rest/images?action=initializeUpload
    PUT  <uploadUrl>                         (the image bytes)
    POST https://api.linkedin.com/rest/posts  -> x-restli-id header

The access token is saved encrypted for your Windows account (DPAPI)
in backend/data, never in .env and never in logs.
"""

import base64
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

import config


SCOPES = ["openid", "profile", "email", "w_member_social"]

# LinkedIn's limit for post text.
MAX_POST_CHARS = 3000

# The Images API accepts these (and under 36,152,320 pixels).
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}

_TIMEOUT = 30.0


class LinkedInError(Exception):
    """A LinkedIn problem explained in plain words (never contains the token)."""


# ============================================================
# SETTINGS (read on each use, so tests and .env changes apply)
# ============================================================

def auth_base() -> str:
    return os.getenv("FATMEE_LINKEDIN_AUTH_BASE", "https://www.linkedin.com").rstrip("/")


def api_base() -> str:
    return os.getenv("FATMEE_LINKEDIN_API_BASE", "https://api.linkedin.com").rstrip("/")


def api_version() -> str:
    return os.getenv("FATMEE_LINKEDIN_VERSION", "202609")


def dry_run() -> bool:
    """True (the default) = check everything, publish nothing."""

    return os.getenv("FATMEE_LINKEDIN_DRY_RUN", "true").strip().lower() not in ("false", "0", "no")


def token_path() -> Path:
    return Path(os.getenv(
        "FATMEE_LINKEDIN_TOKEN_PATH",
        os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "linkedin_token.bin"),
    ))


def client_id() -> str:
    return os.getenv("LINKEDIN_CLIENT_ID", "")


def client_secret() -> str:
    return os.getenv("LINKEDIN_CLIENT_SECRET", "")


def redirect_uri() -> str:
    return os.getenv("LINKEDIN_REDIRECT_URI", "http://127.0.0.1:8000/linkedin/callback")


def is_configured() -> bool:
    return bool(client_id() and client_secret())


# ============================================================
# TOKEN STORAGE (encrypted with Windows DPAPI)
# ============================================================

def _dpapi(data: bytes, protect: bool) -> bytes:
    """Encrypt / decrypt for the current Windows user (CryptProtectData)."""

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    no_ui = 0x01  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), ctypes.c_wchar_p("FATMEE LinkedIn"), None, None, None, no_ui, ctypes.byref(blob_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, no_ui, ctypes.byref(blob_out),
        )

    if not ok:
        raise LinkedInError("Could not read the saved LinkedIn login. Connect again at /linkedin/connect.")

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _encode(data: bytes) -> bytes:
    if os.name == "nt":
        return b"DPAPI1" + _dpapi(data, protect=True)
    # Other systems: no DPAPI. Readable only by your user (chmod 600).
    return b"PLAIN1" + base64.b64encode(data)


def _decode(blob: bytes) -> bytes:
    if blob.startswith(b"DPAPI1"):
        return _dpapi(blob[6:], protect=False)
    if blob.startswith(b"PLAIN1"):
        return base64.b64decode(blob[6:])
    raise LinkedInError("The saved LinkedIn login is damaged. Connect again at /linkedin/connect.")


def save_token(token: dict) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encode(json.dumps(token).encode("utf-8")))

    if os.name != "nt":
        os.chmod(path, 0o600)


def load_token() -> dict | None:
    path = token_path()

    if not path.exists():
        return None

    return json.loads(_decode(path.read_bytes()).decode("utf-8"))


def delete_token() -> bool:
    path = token_path()

    if path.exists():
        path.unlink()
        return True

    return False


# ============================================================
# OAUTH 2.0 (authorization code flow)
# ============================================================

def new_state() -> str:
    """A random value that proves the callback belongs to our login."""

    return secrets.token_urlsafe(24)


def authorization_url(state: str) -> str:

    if not is_configured():
        raise LinkedInError("Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to backend/.env, then restart FATMEE.")

    query = urlencode({
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "state": state,
        "scope": " ".join(SCOPES),
    })

    return f"{auth_base()}/oauth/v2/authorization?{query}"


def exchange_code(code: str, http: httpx.Client | None = None) -> dict:
    """Swap the one-time code for an access token, read the profile, save both."""

    with _client(http) as client:
        response = client.post(
            f"{auth_base()}/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
                "client_id": client_id(),
                "client_secret": client_secret(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise LinkedInError(f"LinkedIn did not accept the login ({_reason(response)}). Try /linkedin/connect again.")

    data = response.json()

    if not data.get("access_token"):
        raise LinkedInError("LinkedIn did not return an access token. Try /linkedin/connect again.")

    token = {
        "access_token": data["access_token"],
        "expires_at": int(time.time()) + int(data.get("expires_in", 0)),
        "scope": data.get("scope", ""),
        "connected_at": int(time.time()),
    }

    if data.get("refresh_token"):
        # Only some apps get refresh tokens; kept if LinkedIn sends one.
        token["refresh_token"] = data["refresh_token"]
        token["refresh_expires_at"] = int(time.time()) + int(data.get("refresh_token_expires_in", 0))

    profile = get_profile(token, http)
    token["person_urn"] = f"urn:li:person:{profile['id']}"
    token["name"] = profile.get("name", "")

    save_token(token)

    return {"name": token["name"], "expires_at": token["expires_at"], "scope": token["scope"]}


# ============================================================
# API CALLS
# ============================================================

def _client(http: httpx.Client | None):
    """Use the given client (tests) or a new one that is closed afterwards."""

    if http is not None:
        class _Borrowed:
            def __enter__(self):
                return http

            def __exit__(self, *exc):
                return False

        return _Borrowed()

    return httpx.Client(timeout=_TIMEOUT)


def _reason(response: httpx.Response) -> str:
    """LinkedIn's error message, short, without anything secret."""

    try:
        body = response.json()
        message = body.get("message") or body.get("error_description") or body.get("error") or ""
    except ValueError:
        message = ""

    return f"HTTP {response.status_code}{': ' + str(message)[:200] if message else ''}"


def _check(response: httpx.Response, action: str) -> None:

    if response.status_code < 400:
        return

    reason = _reason(response)

    if response.status_code == 401:
        raise LinkedInError(f"LinkedIn login expired or was revoked ({reason}). Connect again at /linkedin/connect.")

    if response.status_code == 403:
        raise LinkedInError(
            f"LinkedIn refused to {action} ({reason}). Check that your app has the "
            "'Share on LinkedIn' and 'Sign In with LinkedIn using OpenID Connect' products."
        )

    if response.status_code == 426 or "VERSION" in reason.upper():
        raise LinkedInError(
            f"LinkedIn API version {api_version()} is not accepted ({reason}). "
            "Set FATMEE_LINKEDIN_VERSION to a current YYYYMM version in backend/.env."
        )

    if response.status_code == 429:
        raise LinkedInError("LinkedIn's daily limit was reached. Try again tomorrow.")

    raise LinkedInError(f"LinkedIn could not {action} ({reason}).")


def _valid_token(token: dict | None = None) -> dict:

    token = token or load_token()

    if not token:
        raise LinkedInError("FATMEE is not connected to LinkedIn yet. Open http://127.0.0.1:8000/linkedin/connect to log in.")

    if token.get("expires_at") and token["expires_at"] <= time.time():
        raise LinkedInError("The LinkedIn login has expired (they last about 60 days). Connect again at /linkedin/connect.")

    return token


def _rest_headers(token: dict) -> dict:
    return {
        "Authorization": f"Bearer {token['access_token']}",
        "LinkedIn-Version": api_version(),
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def get_profile(token: dict | None = None, http: httpx.Client | None = None) -> dict:
    """Your LinkedIn id, name, picture and email (OpenID Connect userinfo)."""

    token = _valid_token(token)

    with _client(http) as client:
        response = client.get(
            f"{api_base()}/v2/userinfo",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )

    _check(response, "read your profile")

    data = response.json()

    return {
        "id": data.get("sub", ""),
        "name": data.get("name", ""),
        "given_name": data.get("given_name", ""),
        "family_name": data.get("family_name", ""),
        "picture": data.get("picture", ""),
        "email": data.get("email", ""),
        "email_verified": data.get("email_verified"),
        "locale": data.get("locale", ""),
    }


def status() -> dict:
    """Is FATMEE connected, as whom, until when (no network call, no secrets)."""

    info = {
        "configured": is_configured(),
        "dry_run": dry_run(),
        "api_version": api_version(),
        "redirect_uri": redirect_uri(),
    }

    try:
        token = load_token()
    except LinkedInError as e:
        return {**info, "connected": False, "problem": str(e)}

    if not token:
        return {**info, "connected": False, "next": "Open /linkedin/connect to log in."}

    days_left = (token.get("expires_at", 0) - time.time()) / 86400

    return {
        **info,
        "connected": days_left > 0,
        "name": token.get("name", ""),
        "scopes": token.get("scope", ""),
        "expires_in_days": max(0, round(days_left, 1)),
        **({"next": "The login expired. Open /linkedin/connect again."} if days_left <= 0 else {}),
    }


# ---------------- posts ----------------

_RESERVED = re.compile(r"[|{}@\[\]()<>\\*_~]")
_LONE_HASH = re.compile(r"#(?![A-Za-z0-9_])")


def escape_commentary(text: str) -> str:
    """
    LinkedIn's "little" text format: reserved characters must be escaped
    with a backslash or the post can be cut off. "#word" stays a hashtag;
    a "#" that is not part of a hashtag is escaped.
    """

    text = _RESERVED.sub(lambda m: "\\" + m.group(0), text)
    return _LONE_HASH.sub(r"\\#", text)


def check_post(text: str, image_path: str = "") -> tuple:
    """Plain-word problems with a post before anything is sent. Returns (text, image Path or None)."""

    text = (text or "").strip()

    if not text:
        raise LinkedInError("The post has no text.")

    if len(text) > MAX_POST_CHARS:
        raise LinkedInError(f"The post is {len(text)} characters; LinkedIn allows {MAX_POST_CHARS}.")

    image = None

    if image_path:
        image = Path(image_path)

        if not image.is_file():
            raise LinkedInError(f"The image was not found: {image_path}")

        if image.suffix.lower() not in IMAGE_TYPES:
            raise LinkedInError("LinkedIn accepts PNG, JPG and GIF images.")

    return text, image


def _upload_image(client: httpx.Client, token: dict, image: Path) -> str:

    response = client.post(
        f"{api_base()}/rest/images",
        params={"action": "initializeUpload"},
        headers=_rest_headers(token),
        json={"initializeUploadRequest": {"owner": token["person_urn"]}},
    )
    _check(response, "prepare the image upload")

    value = response.json().get("value", {})
    upload_url, image_urn = value.get("uploadUrl"), value.get("image")

    if not upload_url or not image_urn:
        raise LinkedInError("LinkedIn did not return an image upload link.")

    uploaded = client.put(
        upload_url,
        content=image.read_bytes(),
        headers={
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/octet-stream",
        },
    )
    _check(uploaded, "upload the image")

    return image_urn


def create_post(
    text: str,
    image_path: str = "",
    alt_text: str = "",
    visibility: str = "PUBLIC",
    http: httpx.Client | None = None,
) -> dict:
    """
    Publish a post on your profile (or, in dry run, check it and publish nothing).
    FATMEE only calls this after you approved the exact text and image.
    """

    text, image = check_post(text, image_path)
    visibility = "CONNECTIONS" if str(visibility).upper() == "CONNECTIONS" else "PUBLIC"

    token = _valid_token()

    if "person_urn" not in token:
        raise LinkedInError("The saved LinkedIn login has no profile id. Connect again at /linkedin/connect.")

    if dry_run():
        # Read-only check that the login works; nothing is published.
        profile = get_profile(token, http)

        return {
            "posted": False,
            "dry_run": True,
            "note": "Dry run: nothing was published. Set FATMEE_LINKEDIN_DRY_RUN=false in backend/.env to publish for real.",
            "as": profile.get("name", token.get("name", "")),
            "text": text,
            "characters": len(text),
            "image_path": str(image) if image else None,
            "visibility": visibility,
        }

    with _client(http) as client:

        body = {
            "author": token["person_urn"],
            "commentary": escape_commentary(text),
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if image:
            media = {"id": _upload_image(client, token, image)}

            if alt_text.strip():
                media["altText"] = alt_text.strip()[:4000]

            body["content"] = {"media": media}

        response = client.post(f"{api_base()}/rest/posts", headers=_rest_headers(token), json=body)

        if response.status_code == 409:
            # A conflict means nothing was created (e.g. an image still
            # processing), so one more try cannot post twice.
            time.sleep(3)
            response = client.post(f"{api_base()}/rest/posts", headers=_rest_headers(token), json=body)

    if response.status_code >= 500:
        # A server error doesn't prove the post failed: never retry
        # blindly, it could publish twice.
        raise LinkedInError(
            f"LinkedIn had a server problem ({_reason(response)}). The post may or may not "
            "have been published: check your LinkedIn profile before trying again."
        )

    _check(response, "publish the post")

    post_urn = response.headers.get("x-restli-id", "")

    return {
        "posted": True,
        "dry_run": False,
        "post_urn": post_urn,
        "post_url": f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else "",
        "as": token.get("name", ""),
        "text": text,
        "characters": len(text),
        "image_path": str(image) if image else None,
        "visibility": visibility,
    }
