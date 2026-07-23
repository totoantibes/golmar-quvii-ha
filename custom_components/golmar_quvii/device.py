"""Local device client: opens doors on a panel over the LAN, no cloud.

Uses the per-panel local access key fetched once at setup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import ssl

import aiohttp

from .const import CGI_SECURITY, CGI_USERNAME

_LOGGER = logging.getLogger(__name__)

# self-signed device cert -> no verification
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


class QuviiLocalDevice:
    """Local /tdkcgi controller for one panel."""

    def __init__(self, ip: str, authcode: str, port: int = 443) -> None:
        self.ip = ip
        self.authcode = authcode
        self.port = port

    @property
    def url(self) -> str:
        return f"https://{self.ip}:{self.port}/tdkcgi"

    def _envelope(self, command: str, content: str = "") -> str:
        return ('<?xml version="1.0" encoding="utf-8"?><envelope><header>'
                f"<password>{self.authcode}</password><passwordencode>1</passwordencode>"
                f"<security>{CGI_SECURITY}</security><username>{CGI_USERNAME}</username>"
                f"</header><body><command>{command}</command><content>{content}</content></body></envelope>")

    async def _post(self, session: aiohttp.ClientSession, command: str, content: str = "") -> str:
        async with session.post(
            self.url, data=self._envelope(command, content).encode(),
            headers={"Content-Type": "text/xml"}, ssl=_SSL,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            return await resp.text()

    @staticmethod
    def _error(text: str) -> int | None:
        m = re.search(r"<error>(-?\d+)</error>", text)
        return int(m.group(1)) if m else None

    async def async_open(self, session: aiohttp.ClientSession, door: int, lock: int) -> bool:
        content = f"<door>{door}</door><locknumber>{lock}</locknumber><password>{self.authcode}</password>"
        err = self._error(await self._post(session, "set.device.opendoor", content))
        if err != 0:
            _LOGGER.warning("open door=%s lock=%s on %s returned error=%s", door, lock, self.ip, err)
        return err == 0

    async def async_get_umid(self, session: aiohttp.ClientSession) -> str | None:
        """Return the device umid via get.device.qrcode (also validates the authCode)."""
        try:
            text = await self._post(session, "get.device.qrcode")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None
        m = re.search(r'"u"\s*:\s*"([^"]+)"', text)
        return m.group(1) if m else None

    async def async_reachable(self, session: aiohttp.ClientSession) -> bool:
        try:
            return self._error(await self._post(session, "get.device.status")) is not None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return False

    async def async_get_locks(self, session: aiohttp.ClientSession) -> list[dict]:
        """Enumerate the panel's door/lock relays via get.device.attachInfo.

        Returns one dict per lock relay of each *door* channel (CCTV/light
        channels are skipped):
            {"door": <channel id>, "lock": <1-based relay>, "name": str, "enabled": bool}
        The channel names mirror the official app ("Door1", "General Panel1", ...).
        Empty list on any error so callers can fall back to a static default.
        """
        try:
            text = await self._post(session, "get.device.attachInfo")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return []
        try:
            devlist = json.loads(text)["body"]["content"]["sub-devlist"]
        except (ValueError, TypeError, KeyError):
            return []
        locks: list[dict] = []
        for item in devlist:
            # keep the real door-station channels only (type "chn", camera sub-type);
            # CCTV inputs and lights carry no openable door.
            if item.get("type") != "chn" or item.get("sub-type") != "cam":
                continue
            door = item.get("id")
            if door is None:
                continue
            name = item.get("name") or f"Channel {door}"
            relays = len(item.get("children") or []) or 2
            for lock in range(1, relays + 1):
                locks.append({
                    "door": door,
                    "lock": lock,
                    "name": f"{name} Lock {lock}",
                    "enabled": bool(item.get("enable", 1)),
                })
        return locks


def _local_subnet_prefix() -> str | None:
    """Best-effort /24 prefix of the host's primary LAN address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip.rsplit(".", 1)[0]
    except OSError:
        return None


async def async_discover_ips(devices_by_authcode: dict[str, str]) -> dict[str, str]:
    """Scan the local /24 for /tdkcgi responders and match umids.

    devices_by_authcode: {umid: authcode}
    returns {umid: ip} for the ones found.
    """
    prefix = _local_subnet_prefix()
    if not prefix:
        return {}
    # 1) find hosts with tcp 443 open (fast, concurrent)
    async def _open443(host: str) -> str | None:
        try:
            fut = asyncio.open_connection(host, 443)
            reader, writer = await asyncio.wait_for(fut, timeout=0.8)
            writer.close()
            return host
        except (OSError, asyncio.TimeoutError):
            return None

    hosts = [f"{prefix}.{i}" for i in range(1, 255)]
    open_hosts = [h for h in await asyncio.gather(*[_open443(h) for h in hosts]) if h]

    # 2) for each open host, try each unmatched authcode -> umid
    found: dict[str, str] = {}
    async with aiohttp.ClientSession() as session:
        for host in open_hosts:
            for umid, authcode in devices_by_authcode.items():
                if umid in found:
                    continue
                got = await QuviiLocalDevice(host, authcode).async_get_umid(session)
                if got == umid:
                    found[umid] = host
                    break
    return found
