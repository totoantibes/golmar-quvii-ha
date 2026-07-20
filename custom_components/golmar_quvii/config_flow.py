"""Config flow for Golmar / Quvii Local."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

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


class GolmarQuviiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the account login flow."""

    VERSION = 1

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
                    return self.async_create_entry(
                        title=f"Quvii ({user_input[CONF_ACCOUNT]})",
                        data=user_input,
                    )

        # Defaults = Golmar G2Call+. Other Quvii-SDK brands override app_id / oem_id / region.
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
