import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from dataclasses import asdict

from .const import DOMAIN
from pymbrewclient import BreweryClient, Device

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
    vol.Required("username"): str,
    vol.Required("password"): str,
})

class PymbrewClientConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PymbrewClient."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step for config flow."""
        if user_input is not None:
            return await self._handle_user_input(user_input)

        return self._show_user_form()

    async def _handle_user_input(self, user_input: dict) -> FlowResult:
        """Handle user input for manual configuration."""
        username = user_input["username"]
        password = user_input["password"]
        try:
            # Initialize the client and fetch the brewery overview
            client = BreweryClient(username, password)
            brewery_overview = await self.hass.async_add_executor_job(client.get_brewery_overview)
            _LOGGER.debug(f"Brewery overview: {brewery_overview}")

            # make sure we have some devices
            if not brewery_overview:
                return self._show_user_form(errors={"base": "no_devices_found"})
            else:
                return self.async_create_entry(
                title="Minibrew Pro",
                data={
                    "username": username,
                    "password": password,
                    },
                )

        except ConnectionError:
            return self._show_user_form(errors={"base": "cannot_connect"})
        except Exception as err:
            _LOGGER.error("Unexpected error: %s", err)
            return self._show_user_form(errors={"base": "unknown_error"})


    def _show_user_form(self, errors=None) -> FlowResult:
        """Show the user form for manual configuration."""
        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            errors=errors or {},
        )

    def _is_existing_entry(self, unique_id: str) -> bool:
        """Check if an entry with the given unique ID already exists."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id == unique_id:
                return True
        return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PymbrewClientOptionsFlowHandler()

class PymbrewClientOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for PymbrewClient."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Default value from existing options or fallback to 60
        options_schema = vol.Schema({
            vol.Optional("refresh_interval", default=self.config_entry.options.get("refresh_interval", 60)): int,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )