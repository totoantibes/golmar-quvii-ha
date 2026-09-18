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

from .const import (
    CGI_ENDPOINTS,
    CGI_PATH,
    CGI_SECURITY,
    CGI_USERNAME,
    FINGERPRINT_KEY,
)

_LOGGER = logging.getLogger(__name__)

# Per-host TCP connect budget for the /24 sweep. A panel on an idle LAN answers in
# ~20ms, but discovery usually runs while Home Assistant is starting, which is the
# worst moment on a busy or low-powered host.
DISCOVERY_CONNECT_TIMEOUT = 2.0

# Cap on simultaneous probes. The sweep used to open one connection per host - 254 at
# once - which is what actually broke discovery at startup: on a small host those
# coroutines starve the event loop and hit their deadline even though the panel is
# answering in ~20ms. Raising the per-host timeout does not help, because the time is
# lost waiting for loop time, not for the network.
#
# Measured on a Pi 3B+ (2026-08-31): discovery found nothing while Home Assistant was
# setting up 54 integrations, then succeeded on a later retry with the LAN unchanged -
# panel 443 open in 19-31ms, /tdkcgi answering HTTP 200 in 0.19s, and only three hosts
# on the whole /24 with 443 open. Bounding concurrency costs a little wall clock on a
# sparse subnet and fixes the failure.
DISCOVERY_CONCURRENCY = 16

# self-signed device cert -> no verification
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


class QuviiLocalDevice:
    """Local CGI controller for one panel.

    The CGI answers identically on https/443 and http/80, so which one a panel is
    reachable on is discovered rather than assumed.
    """

    def __init__(self, ip: str, authcode: str, port: int = 443) -> None:
        self.ip = ip
        self.authcode = authcode
        self.port = port

    @property
    def scheme(self) -> str:
        return "https" if self.port == 443 else "http"

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.ip}:{self.port}{CGI_PATH}"

    def _envelope(self, command: str, content: str = "") -> str:
        return ('<?xml version="1.0" encoding="utf-8"?><envelope><header>'
                f"<password>{self.authcode}</password><passwordencode>1</passwordencode>"
                f"<security>{CGI_SECURITY}</security><username>{CGI_USERNAME}</username>"
                f"</header><body><command>{command}</command><content>{content}</content></body></envelope>")

    async def _post(self, session: aiohttp.ClientSession, command: str, content: str = "") -> str:
        async with session.post(
            self.url, data=self._envelope(command, content).encode(),
            headers={"Content-Type": "text/xml"},
            ssl=_SSL if self.scheme == "https" else None,
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


async def async_is_panel(session: aiohttp.ClientSession, ip: str, port: int) -> bool:
    """Is there a Quvii CGI at this address, without presenting the real key?

    A panel replies to a junk key with an <error>401</error> envelope; unrelated
    web servers 404 or drop the connection. Sweeping first with the junk key means
    the panel's real access key is only ever sent to hosts already known to be
    panels - a /24 typically has a dozen hosts listening on port 80 and none of
    them should see the key to your front door.
    """
    probe = QuviiLocalDevice(ip, FINGERPRINT_KEY, port)
    try:
        return probe._error(await probe._post(session, "get.device.status")) is not None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False


def normalise_endpoint(value: object) -> dict | None:
    """Normalise a stored address.

    Before 0.6 only an IP was kept, because only https/443 was ever tried; those
    entries are read back as that port so an upgrade does not lose the panel.
    """
    if isinstance(value, str) and value:
        return {"ip": value, "port": 443}
    if isinstance(value, dict) and value.get("ip"):
        return {"ip": value["ip"], "port": int(value.get("port", 443))}
    return None


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


async def async_verify_ip(ip: str, umid: str, authcode: str, port: int = 443) -> bool:
    """Confirm a known address still answers as this panel.

    One host, so this is cheap enough to run on every refresh - unlike
    async_discover_ips, which sweeps the whole /24. The fingerprint runs first:
    a cached address can have been handed to a different machine by DHCP, and
    that machine must not be sent the key.
    """
    async with aiohttp.ClientSession() as session:
        if not await async_is_panel(session, ip, port):
            return False
        return await QuviiLocalDevice(ip, authcode, port).async_get_umid(session) == umid


async def _open_port(host: str, port: int, sem: asyncio.Semaphore) -> str | None:
    async with sem:
        try:
            fut = asyncio.open_connection(host, port)
            _reader, writer = await asyncio.wait_for(fut, timeout=DISCOVERY_CONNECT_TIMEOUT)
            writer.close()
            return host
        except (OSError, asyncio.TimeoutError):
            return None


async def _match_hosts(
    session: aiohttp.ClientSession,
    hosts: list[str],
    port: int,
    wanted: dict[str, str],
    found: dict[str, dict],
) -> None:
    """Identify which of `hosts` are panels we are looking for, on `port`."""
    for host in hosts:
        if len(found) == len(wanted):
            return
        if not await async_is_panel(session, host, port):
            continue
        for umid, authcode in wanted.items():
            if umid in found:
                continue
            if await QuviiLocalDevice(host, authcode, port).async_get_umid(session) == umid:
                found[umid] = {"ip": host, "port": port}
                break


async def async_discover_ips(
    devices_by_authcode: dict[str, str], extra_hosts: list[str] | None = None
) -> dict[str, dict]:
    """Locate each panel on the network.

    devices_by_authcode: {umid: authcode}
    returns {umid: {"ip": str, "port": int}} for the ones found.

    Addresses in `extra_hosts` are tried first and are not restricted to Home
    Assistant's own subnet, which is the only way to reach a panel that lives on
    a separate VLAN. The /24 sweep then runs on 443, and only falls back to 80 if
    panels are still missing: on a typical home LAN a handful of hosts listen on
    443 and many more on 80, so trying 443 first keeps the work small.
    """
    found: dict[str, dict] = {}
    sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
    prefix = _local_subnet_prefix()

    async with aiohttp.ClientSession() as session:
        for host in extra_hosts or []:
            if len(found) == len(devices_by_authcode):
                break
            for port, _scheme in CGI_ENDPOINTS:
                await _match_hosts(session, [host], port, devices_by_authcode, found)

        if prefix is None:
            if not found:
                _LOGGER.debug("No local subnet to sweep and no extra hosts matched")
            return found

        hosts = [f"{prefix}.{i}" for i in range(1, 255)]
        for port, _scheme in CGI_ENDPOINTS:
            if len(found) == len(devices_by_authcode):
                break
            open_hosts = [
                h for h in await asyncio.gather(*[_open_port(h, port, sem) for h in hosts]) if h
            ]
            _LOGGER.debug("Sweep of %s.0/24 port %s: %s host(s) listening",
                          prefix, port, len(open_hosts))
            await _match_hosts(session, open_hosts, port, devices_by_authcode, found)
    return found
