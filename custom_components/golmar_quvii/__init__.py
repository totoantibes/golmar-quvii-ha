"""Golmar / Quvii Local integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
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
from .device import QuviiLocalDevice, async_discover_ips, async_verify_ip

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON]
# The panel's access key is static (it does not expire), so this refresh only
# picks up added/removed panels. Kept monthly on purpose: each refresh is a cloud
# login, and accounts may be single-session (a re-login can log the phone app
# out). Reload the integration to refresh on demand.
UPDATE_INTERVAL = timedelta(days=30)
# A panel that could not be located on the LAN has unavailable buttons, and waiting
# out UPDATE_INTERVAL means a single bad moment disables the doors for a month.
# This retry re-runs LAN discovery ONLY and never repeats the cloud login, so it is
# safe against the single-session caveat above.
RETRY_INTERVAL = timedelta(minutes=15)

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN + "_ips"


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
        # Discovered IPs are persisted: a sweep that comes up empty at startup must
        # not be able to lose a panel we have already located.
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
        self._ips_loaded = False
        # authCodes are static, so the cloud list is fetched once and then reused.
        # Re-logging in on every refresh can sign the phone app out.
        self._cloud_devices: list[dict] | None = None

    async def _async_update_data(self) -> dict[str, dict]:
        if not self._ips_loaded:
            if stored := await self._store.async_load():
                self.ips.update(stored.get("ips") or {})
            self._ips_loaded = True
        known_ips = dict(self.ips)

        if self._cloud_devices is None:
            try:
                self._cloud_devices = await self.cloud.async_get_devices()
            except QuviiAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except QuviiCloudError as err:
                raise UpdateFailed(str(err)) from err
        devs = self._cloud_devices
        by_auth = {d["umid"]: d["authcode"] for d in devs}

        # Re-check the IPs we already know. A failed check schedules a rescan but
        # deliberately does NOT drop the cached IP - discarding it on one bad moment
        # is exactly what made the buttons vanish until the next monthly refresh.
        recheck_failed = [
            umid
            for umid, ip in self.ips.items()
            if umid in by_auth and not await async_verify_ip(ip, umid, by_auth[umid])
        ]
        for umid in recheck_failed:
            _LOGGER.debug(
                "Panel %s did not answer at its known IP %s; scheduling a rescan",
                umid, self.ips[umid],
            )

        rescan = {u: a for u, a in by_auth.items() if u not in self.ips}
        rescan.update({u: by_auth[u] for u in recheck_failed})
        if rescan:
            try:
                for umid, ip in (await async_discover_ips(rescan)).items():
                    if self.ips.get(umid) != ip:
                        _LOGGER.info("Panel %s located at %s", umid, ip)
                    self.ips[umid] = ip
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

        if self.ips != known_ips:
            await self._store.async_save({"ips": self.ips})

        unresolved = sorted(u for u in by_auth if u not in self.ips)
        if unresolved:
            _LOGGER.warning(
                "Panel(s) %s were not found on the LAN, so their buttons stay "
                "unavailable. Retrying in %s",
                ", ".join(unresolved), RETRY_INTERVAL,
            )
        if unresolved or recheck_failed:
            self.update_interval = RETRY_INTERVAL
        else:
            if self.update_interval != UPDATE_INTERVAL:
                _LOGGER.info("All panels reachable again; back to a %s refresh", UPDATE_INTERVAL)
            self.update_interval = UPDATE_INTERVAL
        return info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = GolmarQuviiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Rebuild the buttons when the user changes the lock selection (options flow).
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so the button list reflects the new selection."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
