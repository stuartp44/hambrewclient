import logging
import requests
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_REALTIME_POLL_INTERVAL,
    DEFAULT_REALTIME_POLL_INTERVAL,
    DOMAIN,
)
from pymbrewclient import BreweryClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
    vol.Required("username"): str,
    vol.Required("password"): str,
})

class PymbrewClientConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PymbrewClient."""

    VERSION = 1

    
    @staticmethod
    def _is_auth_error(err: Exception) -> bool:
        """Return True when an exception indicates invalid credentials."""
        response = getattr(err, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in (401, 403):
            return True
        message = str(err).lower()
        return (
            "401" in message
            or "403" in message
            or "unauthorized" in message
            or "forbidden" in message
        )

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
                title="MiniBrew Pro",
                data={
                    "username": username,
                    "password": password,
                    },
                )

        except requests.exceptions.HTTPError as err:
            if self._is_auth_error(err):
                return self._show_user_form(errors={"base": "invalid_auth"})
            return self._show_user_form(errors={"base": "cannot_connect"})
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


    async def async_step_reauth(self, entry_data):
        """Start reauthentication flow when credentials are invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm reauthentication by validating and storing new credentials."""
        errors = {}
        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]
            try:
                client = BreweryClient(username, password)
                await self.hass.async_add_executor_job(client.get_brewery_overview)

                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                if entry is None:
                    return self.async_abort(reason="unknown_error")

                updated_data = dict(entry.data)
                updated_data.update({"username": username, "password": password})
                self.hass.config_entries.async_update_entry(
                    entry,
                    title="MiniBrew Pro",
                    data=updated_data,
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except requests.exceptions.HTTPError as err:
                if self._is_auth_error(err):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Unexpected reauth error: %s", err)
                errors["base"] = "unknown_error"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=CONFIG_SCHEMA,
            errors=errors,
        )

class PymbrewClientOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for PymbrewClient."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        options_schema = vol.Schema({
            vol.Optional(
                CONF_REALTIME_POLL_INTERVAL,
                default=options.get(CONF_REALTIME_POLL_INTERVAL, DEFAULT_REALTIME_POLL_INTERVAL),
            ): int,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )