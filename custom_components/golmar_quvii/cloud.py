"""Quvii cloud client.

Signs in with a Golmar (or other Quvii-based) account and fetches each panel's
local access key, which is then used for local-only door control. The cloud is
contacted only at setup and for periodic key refresh.
"""
from __future__ import annotations

import hashlib
import logging
import re

import aiohttp

from .const import (
    CLIENT_TYPE,
    DEFAULT_APP_ID,
    DEFAULT_LB,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
)

_LOGGER = logging.getLogger(__name__)


class QuviiCloudError(Exception):
    """Generic cloud error."""


class QuviiAuthError(QuviiCloudError):
    """Login failed (bad account/password)."""


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
        self._client_id = f"003-{app_id}-haquviilocal01"
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
        """Log in and return [{umid, name, model, channels, authcode}, ...]."""
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
        for dev in re.findall(r"<device>(.*?)</device>", xml, re.S):
            def g(tag: str) -> str | None:
                m = re.search(rf"<{tag}>([^<]*)</{tag}>", dev)
                return m.group(1) if m else None
            umid, auth = g("id"), g("out-auth-code")
            if umid and auth:
                out.append({
                    "umid": umid, "name": g("name") or umid, "model": g("model"),
                    "channels": g("channel-num"), "authcode": auth,
                })
        return out
