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
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .cloud import (
    QuviiAuthError,
    QuviiCloud,
    QuviiCloudControl,
    QuviiCloudError,
    QuviiLoginRefused,
    is_issued_password,
    parse_sso_credentials,
)
from .const import (
    BRAND_OPTIONS,
    BRAND_VIMAR,
    BRANDS,
    CONF_ACCOUNT,
    CONF_APP_ID,
    CONF_BRAND,
    CONF_EXTRA_HOSTS,
    CONF_LOCKS,
    CONF_OEM_ID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SERVER_HOST,
    CONF_SSO_JSON,
    CONF_UNLOCK_MODE,
    DEFAULT_APP_ID,
    DEFAULT_BRAND,
    DEFAULT_LOCKS,
    DEFAULT_OEM_ID,
    DEFAULT_REGION,
    DEFAULT_SERVER_HOST,
    DEFAULT_UNLOCK_MODE,
    DOMAIN,
    MODE_AUTO,
    MODE_CLOUD,
    UNLOCK_MODES,
    VIMAR_SSO_URL,
)
from .device import QuviiLocalDevice, async_discover_ips

_LOCK_FIELDS = ("umid", "door", "lock", "name")
# options the user sets on the first screen but that live in entry.options, so
# they stay editable afterwards without reconfiguring the account
_OPTION_KEYS = (CONF_UNLOCK_MODE, CONF_EXTRA_HOSTS)


def _split_hosts(raw: str | None) -> list[str]:
    return [h.strip() for h in (raw or "").replace(";", ",").split(",") if h.strip()]


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


async def _discover_catalog(
    hass: HomeAssistant,
    devices: list[dict],
    extra_hosts: list[str] | None = None,
    skip_discovery: bool = False,
    cloud_control=None,
) -> dict[str, dict]:
    """List each panel's real door/lock relays, by whatever route reaches it.

    Returns {"umid:door:lock": {umid,door,lock,name,label,enabled}}.

    Three sources, in descending order of trust: the panel over the LAN, the
    panel through the cloud, and - for anything still unknown - the static set of
    channels a panel can address. The static set is a guess about one
    installation, so it is used last and never in preference to asking.

    skip_discovery is set for cloud mode, which never contacts the panel over the
    network: sweeping would only delay setup, and the panels that need cloud mode
    are precisely the ones a sweep cannot find.
    """
    names = {d["umid"]: (d.get("name") or d["umid"]) for d in devices}
    by_auth = {d["umid"]: d["authcode"] for d in devices}
    if skip_discovery:
        endpoints: dict[str, dict] = {}
    else:
        try:
            endpoints = await async_discover_ips(by_auth, extra_hosts)
        except Exception:  # noqa: BLE001 - discovery is best-effort
            endpoints = {}

    catalog: dict[str, dict] = {}
    session = async_get_clientsession(hass)
    for umid, endpoint in endpoints.items():
        locks = await QuviiLocalDevice(
            endpoint["ip"], by_auth[umid], endpoint["port"]
        ).async_get_locks(session)
        _add_locks(umid, names[umid], locks, catalog)

    # Ask the cloud about any panel the LAN could not describe. This is the only
    # route for a panel that never exposes a local interface, and without it
    # those installations are stuck with whatever the static set happens to
    # contain - which cost one user their doors 3 and 4 entirely.
    if cloud_control is not None:
        for device in devices:
            umid = device["umid"]
            if any(k.startswith(f"{umid}:") for k in catalog):
                continue
            locks = await cloud_control.async_get_locks(
                session, umid, device.get("dynamic_password") or ""
            )
            if locks:
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
        self._options: dict[str, Any] = {}
        self._catalog: dict[str, dict] = {}
        # first-screen input held while the Vimar SSO step collects credentials
        self._pending: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GolmarQuviiOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # A named brand overrides the advanced fields; only "custom" reads
            # them. The resolved values are what gets stored.
            brand = user_input.get(CONF_BRAND, DEFAULT_BRAND)
            user_input = {**user_input, **BRANDS.get(brand, {})}
            if brand == BRAND_VIMAR and not is_issued_password(
                user_input.get(CONF_PASSWORD, "")
            ):
                # A vimar.com email and password are never what the Quvii cloud
                # accepts, so rather than fail with "invalid account" send the
                # user through the SSO page that hands out the real credentials.
                self._pending = user_input
                return await self.async_step_vimar_sso()
            if not user_input.get(CONF_ACCOUNT) or not user_input.get(CONF_PASSWORD):
                errors["base"] = "missing_credentials"
            elif result := await self._async_sign_in(user_input, errors):
                return result

        # Defaults = Golmar G2Call+. Unlisted Quvii-SDK brands pick "custom" and
        # fill in app_id / oem_id (and server_host if they have their own server).
        # Account and password are optional only because Vimar gets them from
        # the SSO step instead; every other brand is checked above.
        schema = vol.Schema(
            {
                vol.Optional(CONF_BRAND, default=DEFAULT_BRAND): vol.In(BRAND_OPTIONS),
                vol.Optional(CONF_ACCOUNT, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(CONF_REGION, default=DEFAULT_REGION): str,
                vol.Optional(CONF_APP_ID, default=DEFAULT_APP_ID): str,
                vol.Optional(CONF_OEM_ID, default=DEFAULT_OEM_ID): str,
                vol.Optional(CONF_SERVER_HOST, default=DEFAULT_SERVER_HOST): str,
                vol.Optional(CONF_UNLOCK_MODE, default=DEFAULT_UNLOCK_MODE): vol.In(
                    UNLOCK_MODES
                ),
                vol.Optional(CONF_EXTRA_HOSTS, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_vimar_sso(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the credentials Vimar's SSO page shows after signing in."""
        errors: dict[str, str] = {}
        if user_input is not None:
            creds = parse_sso_credentials(user_input.get(CONF_SSO_JSON, ""))
            if creds is None:
                errors["base"] = "invalid_sso_json"
            else:
                account, password = creds
                merged = {**self._pending, CONF_ACCOUNT: account, CONF_PASSWORD: password}
                if result := await self._async_sign_in(merged, errors):
                    return result
        return self.async_show_form(
            step_id="vimar_sso",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SSO_JSON): TextSelector(
                        TextSelectorConfig(multiline=True)
                    )
                }
            ),
            description_placeholders={"url": VIMAR_SSO_URL},
            errors=errors,
        )

    async def _async_sign_in(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Sign in, list the panels and move on to the lock selection.

        Returns the next step, or None with `errors` filled in so the caller can
        show its own form again.
        """
        cloud = QuviiCloud(
            user_input[CONF_ACCOUNT],
            user_input[CONF_PASSWORD],
            user_input.get(CONF_REGION, DEFAULT_REGION),
            user_input.get(CONF_APP_ID, DEFAULT_APP_ID),
            user_input.get(CONF_OEM_ID, DEFAULT_OEM_ID),
            server_host=user_input.get(CONF_SERVER_HOST, DEFAULT_SERVER_HOST),
        )
        try:
            devices = await cloud.async_get_devices()
        except QuviiAuthError:
            errors["base"] = "invalid_auth"
            return None
        except QuviiLoginRefused:
            # Refused, but not for the credentials - most often the account
            # lives on a different regional server than the one asked.
            errors["base"] = "login_refused"
            return None
        except QuviiCloudError:
            errors["base"] = "cannot_connect"
            return None
        if not devices:
            errors["base"] = "no_devices"
            return None

        await self.async_set_unique_id(user_input[CONF_ACCOUNT])
        self._abort_if_unique_id_configured()
        self._data = {k: v for k, v in user_input.items() if k not in _OPTION_KEYS}
        # Sign-in locates the account's real regional server, which
        # may not be the one chosen. Store what worked so the search
        # is not repeated on every future refresh.
        self._data[CONF_REGION] = cloud.resolved_region
        mode = user_input.get(CONF_UNLOCK_MODE, DEFAULT_UNLOCK_MODE)
        self._options = {
            CONF_UNLOCK_MODE: mode,
            CONF_EXTRA_HOSTS: user_input.get(CONF_EXTRA_HOSTS, ""),
        }
        # Cloud modes can ask the panel about itself even with no
        # LAN route to it. Built on the region sign-in actually
        # landed on, not the one that was typed.
        control = None
        if mode in (MODE_CLOUD, MODE_AUTO):
            control = QuviiCloudControl(
                user_input[CONF_ACCOUNT],
                user_input[CONF_PASSWORD],
                cloud.resolved_region,
                user_input.get(CONF_APP_ID, DEFAULT_APP_ID),
                user_input.get(CONF_OEM_ID, DEFAULT_OEM_ID),
            )
        self._catalog = await _discover_catalog(
            self.hass, devices,
            _split_hosts(user_input.get(CONF_EXTRA_HOSTS)),
            skip_discovery=mode == MODE_CLOUD,
            cloud_control=control,
        )
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            locks = _locks_from_keys(self._catalog, user_input.get(CONF_LOCKS, []))
            return self.async_create_entry(
                title=f"Quvii ({self._data[CONF_ACCOUNT]})",
                data=self._data,
                options={**self._options, CONF_LOCKS: locks},
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
        options = self.config_entry.options
        if user_input is not None:
            if not self._catalog:  # defensive: rebuild if instance was recreated
                self._catalog = await self._build_catalog()
            locks = _locks_from_keys(self._catalog, user_input.get(CONF_LOCKS, []))
            return self.async_create_entry(title="", data={
                CONF_LOCKS: locks,
                CONF_UNLOCK_MODE: user_input.get(CONF_UNLOCK_MODE, DEFAULT_UNLOCK_MODE),
                CONF_EXTRA_HOSTS: user_input.get(CONF_EXTRA_HOSTS, ""),
            })

        self._catalog = await self._build_catalog()
        current = [
            f"{x['umid']}:{x['door']}:{x['lock']}"
            for x in options.get(CONF_LOCKS, [])
        ]
        schema = _selection_schema(self._catalog, current).extend(
            {
                vol.Optional(
                    CONF_UNLOCK_MODE,
                    default=options.get(CONF_UNLOCK_MODE, DEFAULT_UNLOCK_MODE),
                ): vol.In(UNLOCK_MODES),
                vol.Optional(
                    CONF_EXTRA_HOSTS, default=options.get(CONF_EXTRA_HOSTS, "")
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

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
        # Panels with no LAN route can still describe themselves through the
        # cloud. Without this, reopening the options on a cloud-only install
        # would keep offering the static set however many doors it really has.
        if coordinator.unlock_mode in (MODE_CLOUD, MODE_AUTO):
            for umid, info in (coordinator.data or {}).items():
                if any(k.startswith(f"{umid}:") for k in catalog):
                    continue
                locks = await coordinator.cloud_control.async_get_locks(
                    session, umid, info.get("dynamic_password") or ""
                )
                if locks:
                    _add_locks(umid, info.get("name") or umid, locks, catalog)
        # panels nothing could describe -> static set of addressable channels
        for umid, info in (coordinator.data or {}).items():
            if not any(k.startswith(f"{umid}:") for k in catalog):
                _fallback_catalog(umid, info.get("name") or umid, catalog)
        # never drop a currently-selected lock just because a panel is offline
        for x in entry.options.get(CONF_LOCKS, []):
            key = f"{x['umid']}:{x['door']}:{x['lock']}"
            catalog.setdefault(key, {**x, "label": x.get("name", key), "enabled": True})
        return catalog
