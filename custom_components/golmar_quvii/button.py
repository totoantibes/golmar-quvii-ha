"""Button entities: one per door/lock, press to open."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_LOCKS, DEFAULT_LOCKS, DOMAIN


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
    def available(self) -> bool:
        return super().available and self._umid in self.coordinator.devices

    async def async_press(self) -> None:
        device = self.coordinator.devices.get(self._umid)
        if device is None:
            raise HomeAssistantError(
                f"Panel {self._umid} not found on the LAN (no IP discovered yet)"
            )
        session = async_get_clientsession(self.hass)
        ok = await device.async_open(session, self._door, self._lock)
        if not ok:
            raise HomeAssistantError(
                f"Panel rejected open door={self._door} lock={self._lock}"
            )
