"""Quvii cloud client.

Signs in with a Golmar (or other Quvii-based) account and fetches each panel's
local access key, which is then used for local-only door control.

A second, optional path lives here too: some firmware only opens the panel's
local CGI while the phone app is streaming video, which leaves nothing to talk
to on the LAN. Those panels can be opened through the Quvii cloud instead - see
QuviiCloudControl. It is off unless the user selects it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone

import aiohttp

from .const import (
    CLIENT_SUFFIX,
    CLIENT_TYPE,
    DEFAULT_APP_ID,
    DEFAULT_LB,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
    OAUTH_HOST_TEMPLATE,
    OAUTH_PATH,
    OPENAPI_CONTROL_PATH,
    OPENAPI_HOST_TEMPLATE,
    TOKEN_DEFAULT_TTL,
    TOKEN_EXPIRY_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


class QuviiCloudError(Exception):
    """Generic cloud error."""


class QuviiAuthError(QuviiCloudError):
    """Login failed (bad account/password)."""


class QuviiDeviceNotRegistered(QuviiCloudError):
    """The panel is not reachable on the cloud control plane.

    Raised when the cloud knows the panel belongs to the account but its IoT
    layer has no registration for it - observed on older firmware, which has no
    cloud control path at all and can only be opened over the LAN.
    """


def _host(region: str, lb: str) -> str:
    return f"https://r{region}-{lb}-sec.qvcloud.net"


class QuviiCloud:
    """Minimal Quvii cloud client (Golmar-ready, any Quvii OEM via app_id/oem_id)."""

    def __init__(
        self,
        account: str,
        password: str,
        region: str = DEFAULT_REGION,
        app_id: str = DEFAULT_APP_ID,
        oem_id: str = DEFAULT_OEM_ID,
        lb: str = DEFAULT_LB,
    ) -> None:
        self._account = account
        self._pw = hashlib.sha256(password.encode()).hexdigest()
        self._region = region
        self._app_id = app_id
        self._oem_id = oem_id
        self._client_id = f"003-{app_id}-{CLIENT_SUFFIX}"
        self._url = _host(region, lb) + "/auth/user?jus_duplex=up"

    def _client(self) -> str:
        return (f"<client><app>{self._app_id}</app><id>{self._client_id}</id>"
                f"<oem>{self._oem_id}</oem><type>{CLIENT_TYPE}</type></client>")

    def _envelope(self, content_class: str, inner: str, command: str, seq: int,
                  session: str | None = None) -> str:
        hdr = self._client() + f"<command>{command}</command><flag>tdkcloud</flag><seq>{seq}</seq>"
        hdr += f"<session>{session}</session>" if session else "<user-data></user-data><version>v1.13</version>"
        return ('<?xml version="1.0" encoding="UTF-8"?><envelope>'
                f'<content class="{content_class}">{inner}</content>'
                f"<header>{hdr}</header></envelope>")

    async def _post(self, session: aiohttp.ClientSession, body: str) -> str:
        async with session.post(
            self._url, data=body.encode(), headers={"Content-Type": "application/xml"}
        ) as resp:
            return await resp.text()

    async def async_get_devices(self) -> list[dict]:
        """Log in and return one dict per panel.

        Keys: umid, name, model, channels, authcode (the local access key), plus
        dynamic_password / password_expired, which only the cloud unlock path
        uses. The dynamic password is short lived - a few days - unlike the
        access key, which is static.
        """
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(
            cookie_jar=jar, headers={"User-Agent": "okhttp/4.9.1"}
        ) as session:
            # 1) login
            inner = (f"<account>{self._account}</account><auth-code></auth-code>"
                     f"<ip-region-id>{self._region}</ip-region-id>"
                     f"<password>{self._pw}</password><auth-type>0</auth-type>")
            text = await self._post(session, self._envelope(
                "com.quvii.qvweb.userauth.bean.request.LoginReqContent", inner, "login", 1))
            res = re.search(r"<result>(-?\d+)</result>", text)
            sid = re.search(r"<session><id>([^<]+)</id>", text)
            if not sid:
                raise QuviiAuthError(f"login failed (result={res.group(1) if res else '?'})")
            session_id = sid.group(1)

            # 2) get-device-list
            inner = ("<count>128</count><filter></filter>"
                     "<manual-accept-device-share>1</manual-accept-device-share>"
                     "<order>0</order><owner></owner><page>0</page>")
            text = await self._post(session, self._envelope(
                "com.quvii.qvweb.userauth.bean.request.DevListReqContent", inner,
                "get-device-list", 2, session=session_id))
            return self._parse_devices(text)

    @staticmethod
    def _parse_devices(xml: str) -> list[dict]:
        out: list[dict] = []
        for dev in re.findall(r"<device>(.*?)</device>", xml, re.DOTALL):
            # dev is bound as a default so the closure reads this iteration's
            # device rather than whichever one the loop ends on
            def g(tag: str, dev: str = dev) -> str | None:
                m = re.search(rf"<{tag}>([^<]*)</{tag}>", dev)
                return m.group(1) if m else None
            umid, auth = g("id"), g("out-auth-code")
            if umid and auth:
                out.append({
                    "umid": umid, "name": g("name") or umid, "model": g("model"),
                    "channels": g("channel-num"), "authcode": auth,
                    "dynamic_password": g("dynamic-password"),
                    "password_expired": g("password-expired"),
                })
        return out


def parse_expiry(value: str | None) -> datetime | None:
    """Parse the device list's password-expired stamp ("YYYY-MM-DD HH:MM:SS").

    The stamp carries no timezone and the service does not document one; it is
    read as UTC. That assumption is unverified - the renewal margin in the
    coordinator is wide enough to absorb a several-hour error, which is why it
    has not been chased further. An unreadable stamp returns None, and the
    coordinator then refreshes conservatively rather than trusting the default.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        _LOGGER.debug("Unrecognised password-expired stamp %r", value)
        return None


def _jwt_expiry(token: str) -> float | None:
    """Read the exp claim out of a JWT, without verifying it.

    The token response carries no expires_in, so the claim is the only statement
    of lifetime available. Unreadable token -> None, and the caller falls back to
    a conservative default.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except (IndexError, ValueError, binascii.Error, TypeError):
        return None
    if not isinstance(exp, (int, float)):
        return None
    # seconds or milliseconds, depending on the issuer
    return exp / 1000 if exp > 10_000_000_000 else float(exp)


class QuviiCloudControl:
    """Opens doors through the Quvii cloud instead of over the LAN.

    For firmware that keeps the local CGI closed unless the app is streaming.
    Every open costs a round trip to the internet, so this is a fallback rather
    than the default: it is slower than the LAN path and stops working when the
    connection or the vendor does.
    """

    def __init__(
        self,
        account: str,
        password: str,
        region: str = DEFAULT_REGION,
        app_id: str = DEFAULT_APP_ID,
        oem_id: str = DEFAULT_OEM_ID,
    ) -> None:
        self._account = account
        self._pw = hashlib.sha256(password.encode()).hexdigest()
        self._region = region
        self._app_id = app_id
        self._oem_id = oem_id
        self._client_id = f"003-{app_id}-{CLIENT_SUFFIX}"
        self._oauth_url = OAUTH_HOST_TEMPLATE.format(region=region) + OAUTH_PATH
        self._control_url = (
            OPENAPI_HOST_TEMPLATE.format(region=region) + OPENAPI_CONTROL_PATH
        )
        self._token: str | None = None
        self._token_expires: float = 0.0

    @staticmethod
    def _payload_error(payload: object) -> int | None:
        """Return the panel's own error code from a control response, if present.

        The outer `result` only says whether the cloud accepted the command and
        dispatched it. The panel's own answer rides along in `payload` as a
        JSON-encoded string, so `{"result":0,"payload":"{\\"error\\":401}"}` is a
        command the cloud delivered and the panel refused.

        None means there was nothing readable, in which case the outer result is
        all the caller has to go on.
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        for candidate in (payload, payload.get("body")):
            if not isinstance(candidate, dict):
                continue
            err = candidate.get("error")
            # bool is an int subclass, and a JSON true here would not be a code
            if isinstance(err, bool) or not isinstance(err, (int, float)):
                continue
            return int(err)
        return None

    async def _async_token(self, session: aiohttp.ClientSession) -> str:
        """Return a valid access token, minting one if the cached one is stale.

        Minting is deliberately lazy: it happens on the first cloud unlock and
        then roughly hourly while doors are being opened, rather than on a timer.

        The response also carries a refresh token, which this deliberately does
        not use. Re-running the password grant is one request either way and
        keeps a single verified code path; the refresh grant would add a second
        one for no gain. If re-authenticating this often ever turns out to
        disturb the phone app's session, the refresh grant is the fix.
        """
        if self._token and time.time() < self._token_expires:
            return self._token
        params = {
            "grant_type": "password",
            "client_id": self._client_id,
            "client_type": CLIENT_TYPE,
            "oemid": self._oem_id,
            "appid": self._app_id,
            "usr": self._account,
            "pwd": self._pw,
            "region_id": self._region,
            "client_flag": "1",
        }
        try:
            async with session.get(
                self._oauth_url, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            # The account and the password hash travel in this URL's query
            # string, and aiohttp puts the request URL into several of its
            # exception messages. Neither the message nor a chained traceback
            # may carry it into the logs or the UI, so only the exception type
            # is reported and the cause is deliberately dropped.
            raise QuviiCloudError(
                f"token request failed ({type(err).__name__})"
            ) from None

        try:
            doc = json.loads(body)
        except ValueError:
            raise QuviiCloudError(f"token response was not JSON (http {status})") from None
        token = doc.get("access_token")
        if not token:
            # The endpoint answers 403 result=-20 for a bad account or password.
            raise QuviiAuthError(
                f"no token issued (http {status}, result={doc.get('result')})"
            )
        expiry = _jwt_expiry(token)
        self._token = token
        self._token_expires = (
            expiry - TOKEN_EXPIRY_MARGIN if expiry else time.time() + TOKEN_DEFAULT_TTL
        )
        return token

    async def async_open(
        self,
        session: aiohttp.ClientSession,
        umid: str,
        dynamic_password: str,
        authcode: str,
        door: int,
        lock: int,
    ) -> None:
        """Open one lock through the cloud. Raises on anything but success."""
        if not dynamic_password:
            raise QuviiCloudError(
                "no dynamic password for this panel; refresh the integration"
            )
        token = await self._async_token(session)
        payload = {
            "deviceId": umid,
            "password": dynamic_password,
            "command": "set.device.opendoor",
            "content": {"door": door, "locknumber": lock, "password": authcode},
        }
        try:
            async with session.post(
                self._control_url, json=payload,
                headers={"token": token},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise QuviiCloudError(f"cloud open failed: {err}") from err

        try:
            doc = json.loads(body)
        except ValueError:
            raise QuviiCloudError("cloud open returned a non-JSON response") from None

        result = doc.get("result")
        if result == 0:
            # Dispatch succeeded; the panel's own verdict is a second, nested
            # response. Accepting the outer result alone would report a refused
            # command as a successful open.
            inner = self._payload_error(doc.get("payload"))
            if inner is None:
                _LOGGER.debug(
                    "Cloud accepted the command but returned no readable device "
                    "payload (%r); treating the dispatch as success",
                    doc.get("payload"),
                )
                return
            if inner == 0:
                return
            if inner == 401:
                raise QuviiAuthError(
                    "the panel refused the command's credentials (error 401); the "
                    "stored keys may be stale - reload the integration"
                )
            raise QuviiCloudError(f"the panel refused the command (error {inner})")
        message = doc.get("message") or ""
        if result == -1:
            # token rejected - drop it so the next attempt mints a fresh one
            self._token = None
            self._token_expires = 0.0
            raise QuviiAuthError(f"cloud rejected the token: {message}")
        if result == 1:
            raise QuviiCloudError(f"panel {umid} is not bound to this account")
        if result == 3 or "未注册" in message:
            raise QuviiDeviceNotRegistered(
                f"panel {umid} is not registered on the Quvii control plane, so it "
                "cannot be opened through the cloud; use local mode"
            )
        raise QuviiCloudError(f"cloud open failed (result={result}): {message}")
