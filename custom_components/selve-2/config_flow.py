from __future__ import annotations

from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from .const import DOMAIN, CONF_NAME

from .server import SeleveHomeServer


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]):
    """Validate connection and return server_info object."""
    host = data[CONF_HOST]
    password = data[CONF_PASSWORD]
    api = SeleveHomeServer(host, password)
    server_info = await hass.async_add_executor_job(api.get_server_info)
    return server_info


class SelveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2
    _recommended_name: str | None = None
    _first_step_data: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            errors: dict[str, str] = {}
            try:
                server_info = await _validate_input(self.hass, user_input)
            except Exception:  # broad catch to show error
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"server_{user_input[CONF_HOST]}")
                self._abort_if_unique_id_configured()
                # Store data for next step
                self._first_step_data = user_input
                self._recommended_name = server_info.name
                return await self.async_step_name()

            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=user_input.get(CONF_HOST)): str,
                        vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD)): str,
                        vol.Optional(CONF_NAME, default=user_input.get(CONF_NAME, "")): str,
                    }
                ),
                errors=errors,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
        )

    async def async_step_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            server_name = user_input.get(CONF_NAME, "").strip()
            data = {**(self._first_step_data or {})}
            if server_name:
                data[CONF_NAME] = server_name
            title = server_name or (
                self._recommended_name or "Selve Home Server")
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_NAME,
                        default=self._recommended_name or "Selve Home Server",
                    ): str
                }
            ),
            description_placeholders={
                "suggestion": self._recommended_name or ""},
        )
