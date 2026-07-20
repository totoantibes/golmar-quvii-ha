"""Golmar / Quvii Local integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud import QuviiAuthError, QuviiCloud, QuviiCloudError
from .const import (
    CONF_ACCOUNT,
    CONF_APP_ID,
    CONF_OEM_ID,
    CONF_PASSWORD,
    CONF_REGION,
    DEFAULT_APP_ID,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
    DOMAIN,
)
from .device import QuviiLocalDevice, async_discover_ips

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON]
# The panel's access key is static (it does not expire), so this refresh only
# picks up added/removed panels. Kept monthly on purpose: each refresh is a cloud
# login, and accounts may be single-session (a re-login can log the phone app
# out). Reload the integration to refresh on demand.
UPDATE_INTERVAL = timedelta(days=30)


class GolmarQuviiCoordinator(DataUpdateCoordinator):
    """Refreshes the cloud device list (authCodes) and keeps local device clients."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.entry = entry
        self.cloud = QuviiCloud(
            entry.data[CONF_ACCOUNT],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_REGION, DEFAULT_REGION),
            entry.data.get(CONF_APP_ID, DEFAULT_APP_ID),
            entry.data.get(CONF_OEM_ID, DEFAULT_OEM_ID),
        )
        self.ips: dict[str, str] = {}          # umid -> LAN ip
        self.devices: dict[str, QuviiLocalDevice] = {}  # umid -> local client

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            devs = await self.cloud.async_get_devices()
        except QuviiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except QuviiCloudError as err:
            raise UpdateFailed(str(err)) from err

        by_auth = {d["umid"]: d["authcode"] for d in devs}
        missing = {u: a for u, a in by_auth.items() if u not in self.ips}
        if missing:
            try:
                self.ips.update(await async_discover_ips(missing))
            except Exception:  # noqa: BLE001 - discovery is best-effort
                _LOGGER.exception("LAN discovery failed")

        info: dict[str, dict] = {}
        for d in devs:
            umid = d["umid"]
            ip = self.ips.get(umid)
            if ip:
                self.devices[umid] = QuviiLocalDevice(ip, d["authcode"])
            d["ip"] = ip
            info[umid] = d
        return info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = GolmarQuviiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
