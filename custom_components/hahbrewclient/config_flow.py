import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from dataclasses import asdict

from .const import DOMAIN
from pymbrewclient import BreweryClient

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

            # Create entries for each device in the brewery overview
            for state, devices in asdict(brewery_overview).items():
                for device in devices:
                    unique_id = device.uuid
                    await self.async_set_unique_id(unique_id, raise_on_progress=False)
                    if self._is_existing_entry(unique_id):
                        continue

                    self._create_device_entry(device, state)

            return self.async_abort(reason="devices_configured")  # All devices are configured
        except ConnectionError:
            return self._show_user_form(errors={"base": "cannot_connect"})
        except RuntimeError:
            return self._show_user_form(errors={"base": "unknown_error"})

    def _create_device_entry(self, device, state: str):
        """Create a config entry for a single device."""
        self.async_create_entry(
            title=f"{device.title} ({state})",
            data={
                "uuid": device.uuid,
                "serial_number": device.serial_number,
                "device_type": device.device_type,
                "state": state,
                "beer_name": device.beer_name,
                "current_temp": device.current_temp,
                "target_temp": device.target_temp,
                "online": device.online,
            },
        )

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
        return PymbrewClientOptionsFlowHandler(config_entry)  # Implement options flow if needed