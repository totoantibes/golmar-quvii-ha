"""Button entities: one per door/lock, press to open."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud import QuviiCloudError
from .const import (
    CONF_LOCKS,
    DEFAULT_LOCKS,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[GolmarQuviiButton] = []

    selected = entry.options.get(CONF_LOCKS)
    if selected is not None:
        # User picked the panels/locks in the config (or options) flow.
        # An explicit empty list means "no buttons" and is honoured as-is.
        for lk in selected:
            info = coordinator.data.get(lk["umid"])
            if info is None:
                continue
            entities.append(GolmarQuviiButton(
                coordinator, lk["umid"], info, lk["door"], lk["lock"], lk["name"]))
    else:
        # Legacy entry (created before the selection step): fall back to the
        # static default set for every discovered device.
        for umid, info in coordinator.data.items():
            for door, lock, label in DEFAULT_LOCKS:
                entities.append(GolmarQuviiButton(coordinator, umid, info, door, lock, label))

    async_add_entities(entities)


class GolmarQuviiButton(CoordinatorEntity, ButtonEntity):
    """A single open-door button."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:gate-open"

    def __init__(self, coordinator, umid, info, door, lock, label) -> None:
        super().__init__(coordinator)
        self._umid = umid
        self._door = door
        self._lock = lock
        self._attr_name = label
        self._attr_unique_id = f"{umid}_d{door}_l{lock}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, umid)},
            name=info.get("name") or umid,
            model=info.get("model"),
            manufacturer="Golmar / Quvii",
        )

    @property
    def _local(self):
        return self.coordinator.devices.get(self._umid)

    @property
    def available(self) -> bool:
        """Available when at least one of the configured paths can carry a press.

        Local mode still depends on having found the panel on the network. Cloud
        mode does not - tying its availability to a LAN address would leave the
        buttons unavailable for exactly the panels that need the cloud.
        """
        if not super().available:
            return False
        mode = self.coordinator.unlock_mode
        if mode == MODE_LOCAL:
            return self._local is not None
        if mode == MODE_CLOUD:
            return self.coordinator.cloud_ready(self._umid)
        return self._local is not None or self.coordinator.cloud_ready(self._umid)

    @property
    def extra_state_attributes(self) -> dict:
        info = (self.coordinator.data or {}).get(self._umid) or {}
        address = f"{info['ip']}:{info['port']}" if info.get("ip") else None
        return {
            "unlock_mode": self.coordinator.unlock_mode,
            "local_address": address,
            "cloud_available": self.coordinator.cloud_ready(self._umid),
        }

    async def _async_open_cloud(self, session) -> None:
        info = (self.coordinator.data or {}).get(self._umid) or {}
        await self.coordinator.cloud_control.async_open(
            session,
            self._umid,
            info.get("dynamic_password") or "",
            info.get("authcode") or "",
            self._door,
            self._lock,
        )

    async def async_press(self) -> None:
        session = async_get_clientsession(self.hass)
        mode = self.coordinator.unlock_mode
        device = self._local

        if mode == MODE_CLOUD:
            try:
                await self._async_open_cloud(session)
            except QuviiCloudError as err:
                raise HomeAssistantError(str(err)) from err
            return

        if device is not None:
            try:
                if await device.async_open(session, self._door, self._lock):
                    return
                local_error = f"panel rejected open door={self._door} lock={self._lock}"
            except Exception as err:  # noqa: BLE001 - any local failure may be retried via cloud
                local_error = str(err) or type(err).__name__
        else:
            local_error = f"panel {self._umid} was not found on the network"

        if mode == MODE_LOCAL:
            raise HomeAssistantError(local_error)

        # MODE_AUTO: the local path is the fast one, but on firmware that only
        # opens its local interface while the app streams video it is simply
        # absent most of the time, so a failure there is expected rather than
        # exceptional and the cloud is tried next.
        _LOGGER.debug("Local open failed (%s); trying the cloud", local_error)
        try:
            await self._async_open_cloud(session)
        except QuviiCloudError as err:
            raise HomeAssistantError(
                f"could not open door={self._door} lock={self._lock}: "
                f"local failed ({local_error}); cloud failed ({err})"
            ) from err
