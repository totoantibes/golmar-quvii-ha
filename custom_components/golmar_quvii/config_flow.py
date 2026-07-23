"""Config flow for Golmar / Quvii Local."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import QuviiAuthError, QuviiCloud, QuviiCloudError
from .const import (
    CONF_ACCOUNT,
    CONF_APP_ID,
    CONF_LOCKS,
    CONF_OEM_ID,
    CONF_PASSWORD,
    CONF_REGION,
    DEFAULT_APP_ID,
    DEFAULT_LOCKS,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
    DOMAIN,
)
from .device import QuviiLocalDevice, async_discover_ips

_LOCK_FIELDS = ("umid", "door", "lock", "name")


def _fallback_catalog(umid: str, name: str, catalog: dict[str, dict]) -> None:
    """Add the static default door/lock set for a panel we couldn't enumerate."""
    for door, lock, label in DEFAULT_LOCKS:
        catalog[f"{umid}:{door}:{lock}"] = {
            "umid": umid, "door": door, "lock": lock,
            "name": label, "label": f"{name} · {label}", "enabled": door == 1,
        }


def _add_locks(umid: str, name: str, locks: list[dict], catalog: dict[str, dict]) -> None:
    for lk in locks:
        catalog[f"{umid}:{lk['door']}:{lk['lock']}"] = {
            "umid": umid, "door": lk["door"], "lock": lk["lock"],
            "name": lk["name"], "label": f"{name} · {lk['name']}",
            "enabled": lk["enabled"],
        }


async def _discover_catalog(hass: HomeAssistant, devices: list[dict]) -> dict[str, dict]:
    """Scan the LAN and list each panel's real door/lock relays.

    Returns {"umid:door:lock": {umid,door,lock,name,label,enabled}}. Panels that
    can't be reached fall back to the static DEFAULT_LOCKS so the user can still
    pick something and refine later via the options flow.
    """
    names = {d["umid"]: (d.get("name") or d["umid"]) for d in devices}
    by_auth = {d["umid"]: d["authcode"] for d in devices}
    try:
        ips = await async_discover_ips(by_auth)
    except Exception:  # noqa: BLE001 - discovery is best-effort
        ips = {}

    catalog: dict[str, dict] = {}
    session = async_get_clientsession(hass)
    for umid, ip in ips.items():
        locks = await QuviiLocalDevice(ip, by_auth[umid]).async_get_locks(session)
        _add_locks(umid, names[umid], locks, catalog)
    for umid, name in names.items():
        if not any(k.startswith(f"{umid}:") for k in catalog):
            _fallback_catalog(umid, name, catalog)
    return catalog


def _selection_schema(catalog: dict[str, dict], current_keys: list[str]) -> vol.Schema:
    options = {k: v["label"] for k, v in catalog.items()}
    default = [k for k in current_keys if k in catalog] or [
        k for k, v in catalog.items() if v["enabled"]
    ]
    return vol.Schema(
        {vol.Optional(CONF_LOCKS, default=default): cv.multi_select(options)}
    )


def _locks_from_keys(catalog: dict[str, dict], keys: list[str]) -> list[dict]:
    return [{f: catalog[k][f] for f in _LOCK_FIELDS} for k in keys if k in catalog]


class GolmarQuviiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the account login flow, then let the user pick which locks to add."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._catalog: dict[str, dict] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GolmarQuviiOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cloud = QuviiCloud(
                user_input[CONF_ACCOUNT],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_REGION, DEFAULT_REGION),
                user_input.get(CONF_APP_ID, DEFAULT_APP_ID),
                user_input.get(CONF_OEM_ID, DEFAULT_OEM_ID),
            )
            try:
                devices = await cloud.async_get_devices()
            except QuviiAuthError:
                errors["base"] = "invalid_auth"
            except QuviiCloudError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(user_input[CONF_ACCOUNT])
                    self._abort_if_unique_id_configured()
                    self._data = user_input
                    self._catalog = await _discover_catalog(self.hass, devices)
                    return await self.async_step_select()

        # Defaults = Golmar G2Call+. Other Quvii-SDK brands override app_id / oem_id.
        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_REGION, default=DEFAULT_REGION): str,
                vol.Optional(CONF_APP_ID, default=DEFAULT_APP_ID): str,
                vol.Optional(CONF_OEM_ID, default=DEFAULT_OEM_ID): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            locks = _locks_from_keys(self._catalog, user_input.get(CONF_LOCKS, []))
            return self.async_create_entry(
                title=f"Quvii ({self._data[CONF_ACCOUNT]})",
                data=self._data,
                options={CONF_LOCKS: locks},
            )
        return self.async_show_form(
            step_id="select", data_schema=_selection_schema(self._catalog, [])
        )


class GolmarQuviiOptionsFlow(OptionsFlow):
    """Re-pick which panels/locks appear as buttons, any time after install."""

    def __init__(self) -> None:
        self._catalog: dict[str, dict] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if not self._catalog:  # defensive: rebuild if instance was recreated
                self._catalog = await self._build_catalog()
            locks = _locks_from_keys(self._catalog, user_input.get(CONF_LOCKS, []))
            return self.async_create_entry(title="", data={CONF_LOCKS: locks})

        self._catalog = await self._build_catalog()
        current = [
            f"{x['umid']}:{x['door']}:{x['lock']}"
            for x in self.config_entry.options.get(CONF_LOCKS, [])
        ]
        return self.async_show_form(
            step_id="init", data_schema=_selection_schema(self._catalog, current)
        )

    async def _build_catalog(self) -> dict[str, dict]:
        """Build the catalog from the running coordinator (no LAN re-scan)."""
        entry = self.config_entry
        coordinator = self.hass.data[DOMAIN][entry.entry_id]
        session = async_get_clientsession(self.hass)
        catalog: dict[str, dict] = {}
        for umid, dev in coordinator.devices.items():
            name = (coordinator.data.get(umid) or {}).get("name") or umid
            locks = await dev.async_get_locks(session)
            _add_locks(umid, name, locks, catalog)
        # panels the cloud lists but that are unreachable now -> static default set
        for umid, info in (coordinator.data or {}).items():
            if not any(k.startswith(f"{umid}:") for k in catalog):
                _fallback_catalog(umid, info.get("name") or umid, catalog)
        # never drop a currently-selected lock just because a panel is offline
        for x in entry.options.get(CONF_LOCKS, []):
            key = f"{x['umid']}:{x['door']}:{x['lock']}"
            catalog.setdefault(key, {**x, "label": x.get("name", key), "enabled": True})
        return catalog
