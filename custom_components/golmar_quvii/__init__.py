"""Golmar / Quvii Local integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud import (
    QuviiAuthError,
    QuviiCloud,
    QuviiCloudControl,
    QuviiCloudError,
    parse_expiry,
)
from .const import (
    CONF_ACCOUNT,
    CONF_APP_ID,
    CONF_EXTRA_HOSTS,
    CONF_OEM_ID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_UNLOCK_MODE,
    DEFAULT_APP_ID,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
    DEFAULT_UNLOCK_MODE,
    DOMAIN,
    MODE_AUTO,
    MODE_CLOUD,
    MODE_LOCAL,
)
from .device import (
    QuviiLocalDevice,
    async_discover_ips,
    async_verify_ip,
    normalise_endpoint,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON]
# The panel's local access key is static (it does not expire), so in local mode
# this refresh only picks up added/removed panels. Kept monthly on purpose: each
# refresh is a cloud login, and accounts may be single-session (a re-login can
# log the phone app out). Reload the integration to refresh on demand.
UPDATE_INTERVAL = timedelta(days=30)
# A panel that could not be located has unavailable buttons, and waiting out
# UPDATE_INTERVAL means a single bad moment disables the doors for a month. This
# retry re-runs discovery ONLY and never repeats the cloud login, so it is safe
# against the single-session caveat above.
RETRY_INTERVAL = timedelta(minutes=15)
# Cloud unlock rides on the panel's dynamic password, which - unlike the local
# access key - expires within days (measured: a shade under a week). Renewing it
# means repeating the cloud login, so it is done once per expiry window rather
# than on the monthly cycle, and never at all in local mode.
DYNAMIC_PASSWORD_MARGIN = timedelta(hours=12)
MIN_CLOUD_INTERVAL = timedelta(hours=1)

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN + "_ips"


class GolmarQuviiCoordinator(DataUpdateCoordinator):
    """Refreshes the cloud device list and keeps a client per panel."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.entry = entry
        account = entry.data[CONF_ACCOUNT]
        password = entry.data[CONF_PASSWORD]
        region = entry.data.get(CONF_REGION, DEFAULT_REGION)
        app_id = entry.data.get(CONF_APP_ID, DEFAULT_APP_ID)
        oem_id = entry.data.get(CONF_OEM_ID, DEFAULT_OEM_ID)
        self.cloud = QuviiCloud(account, password, region, app_id, oem_id)
        self.cloud_control = QuviiCloudControl(account, password, region, app_id, oem_id)
        self.endpoints: dict[str, dict] = {}          # umid -> {"ip", "port"}
        self.devices: dict[str, QuviiLocalDevice] = {}  # umid -> local client
        # Discovered addresses are persisted: a sweep that comes up empty at startup
        # must not be able to lose a panel we have already located.
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
        self._endpoints_loaded = False
        # The local access key is static, so the cloud list is fetched once and then
        # reused. Re-logging in on every refresh can sign the phone app out.
        self._cloud_devices: list[dict] | None = None

    @property
    def unlock_mode(self) -> str:
        return self.entry.options.get(CONF_UNLOCK_MODE, DEFAULT_UNLOCK_MODE)

    @property
    def extra_hosts(self) -> list[str]:
        raw = self.entry.options.get(CONF_EXTRA_HOSTS) or ""
        return [h.strip() for h in raw.replace(";", ",").split(",") if h.strip()]

    def cloud_ready(self, umid: str) -> bool:
        """Can this panel be opened through the cloud right now?"""
        info = (self.data or {}).get(umid) or {}
        return bool(info.get("dynamic_password"))

    async def _async_load_endpoints(self) -> None:
        if self._endpoints_loaded:
            return
        if stored := await self._store.async_load():
            raw = stored.get("endpoints") or stored.get("ips") or {}
            for umid, value in raw.items():
                if endpoint := normalise_endpoint(value):
                    self.endpoints[umid] = endpoint
        self._endpoints_loaded = True

    def _next_expiry(self, devices: list[dict]) -> datetime | None:
        stamps = [
            expiry
            for expiry in (parse_expiry(d.get("password_expired")) for d in devices)
            if expiry is not None
        ]
        return min(stamps) if stamps else None

    async def _async_refresh_cloud_devices(self, force: bool) -> list[dict]:
        if self._cloud_devices is not None and not force:
            return self._cloud_devices
        try:
            self._cloud_devices = await self.cloud.async_get_devices()
        except QuviiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except QuviiCloudError as err:
            if self._cloud_devices is not None:
                # A failed renewal is not a reason to drop panels we already know.
                _LOGGER.warning("Could not refresh the cloud device list: %s", err)
                return self._cloud_devices
            raise UpdateFailed(str(err)) from err
        return self._cloud_devices

    async def _async_update_data(self) -> dict[str, dict]:
        await self._async_load_endpoints()
        known = dict(self.endpoints)
        mode = self.unlock_mode
        wants_cloud = mode in (MODE_CLOUD, MODE_AUTO)
        wants_local = mode in (MODE_LOCAL, MODE_AUTO)

        # Renew the device list when a dynamic password is about to expire - only
        # cloud unlocking uses it, so local-mode installs keep the monthly cycle.
        stale = False
        if wants_cloud and self._cloud_devices is not None:
            expiry = self._next_expiry(self._cloud_devices)
            stale = expiry is None or expiry - DYNAMIC_PASSWORD_MARGIN <= datetime.now(timezone.utc)
        devs = await self._async_refresh_cloud_devices(force=stale)
        by_auth = {d["umid"]: d["authcode"] for d in devs}

        recheck_failed: list[str] = []
        if wants_local:
            # Re-check the addresses we already know. A failed check schedules a
            # rescan but deliberately does NOT drop the cached address - discarding
            # it on one bad moment is exactly what made the buttons vanish until the
            # next monthly refresh.
            recheck_failed = [
                umid
                for umid, endpoint in self.endpoints.items()
                if umid in by_auth
                and not await async_verify_ip(
                    endpoint["ip"], umid, by_auth[umid], endpoint["port"]
                )
            ]
            for umid in recheck_failed:
                _LOGGER.debug(
                    "Panel %s did not answer at its known address %s; scheduling a rescan",
                    umid, self.endpoints[umid]["ip"],
                )

            rescan = {u: a for u, a in by_auth.items() if u not in self.endpoints}
            rescan.update({u: by_auth[u] for u in recheck_failed})
            if rescan:
                try:
                    discovered = await async_discover_ips(rescan, self.extra_hosts)
                except Exception:  # noqa: BLE001 - discovery is best-effort
                    _LOGGER.exception("Panel discovery failed")
                else:
                    for umid, endpoint in discovered.items():
                        if self.endpoints.get(umid) != endpoint:
                            _LOGGER.info("Panel %s located at %s:%s",
                                         umid, endpoint["ip"], endpoint["port"])
                        self.endpoints[umid] = endpoint

        info: dict[str, dict] = {}
        self.devices = {}
        for d in devs:
            umid = d["umid"]
            endpoint = self.endpoints.get(umid) if wants_local else None
            if endpoint:
                self.devices[umid] = QuviiLocalDevice(
                    endpoint["ip"], d["authcode"], endpoint["port"]
                )
            info[umid] = {**d, "ip": (endpoint or {}).get("ip"),
                          "port": (endpoint or {}).get("port")}

        if self.endpoints != known:
            await self._store.async_save({"endpoints": self.endpoints})

        self._apply_interval(by_auth, recheck_failed, devs, wants_local, wants_cloud, mode)
        return info

    def _apply_interval(self, by_auth, recheck_failed, devs, wants_local, wants_cloud, mode) -> None:
        """Pick the next refresh: soon if something is unresolved, else lazily."""
        unresolved = sorted(u for u in by_auth if u not in self.endpoints) if wants_local else []
        if unresolved:
            if mode == MODE_AUTO:
                _LOGGER.warning(
                    "Panel(s) %s were not found on the network; their buttons will use "
                    "the cloud until a local address is found. Retrying in %s",
                    ", ".join(unresolved), RETRY_INTERVAL,
                )
            else:
                _LOGGER.warning(
                    "Panel(s) %s were not found on the network, so their buttons stay "
                    "unavailable. Retrying in %s", ", ".join(unresolved), RETRY_INTERVAL,
                )

        target = UPDATE_INTERVAL
        if wants_cloud:
            expiry = self._next_expiry(devs)
            if expiry:
                due = expiry - DYNAMIC_PASSWORD_MARGIN - datetime.now(timezone.utc)
                target = max(MIN_CLOUD_INTERVAL, min(target, due))
        if unresolved or recheck_failed:
            target = min(target, RETRY_INTERVAL)
        elif self.update_interval != target:
            _LOGGER.info("All panels reachable; next refresh in %s", target)
        self.update_interval = target


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = GolmarQuviiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Setup runs while Home Assistant is still starting, which is exactly when the
    # sweep is least likely to succeed. If it found nothing, try again the moment
    # startup finishes instead of waiting out RETRY_INTERVAL - that turns a 15 minute
    # window of unavailable buttons into a few seconds. If Home Assistant has already
    # started, async_at_started fires straight away and this is a no-op re-check.
    if coordinator.unlock_mode != MODE_CLOUD and not coordinator.devices:
        async def _retry_when_started(_hass: HomeAssistant) -> None:
            _LOGGER.debug("Home Assistant has started; retrying panel discovery")
            await coordinator.async_request_refresh()

        entry.async_on_unload(async_at_started(hass, _retry_when_started))

    # Rebuild the buttons when the user changes the lock selection (options flow).
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so the buttons reflect the new options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
