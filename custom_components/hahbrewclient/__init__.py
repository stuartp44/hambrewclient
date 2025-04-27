import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from .const import DOMAIN
from pymbrewclient import breweryclient

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Minibrew integration."""
    _LOGGER.debug("Setting up Minibrew integration")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Minibrew from a config entry."""
    minibrew_username = entry.data["minibrew_username"]
    minibrew_password = entry.data["minibrew_password"]

    try:
        minibrew_client = breweryclient(username=minibrew_username, password=minibrew_password)
        _LOGGER.debug(f"Minibrew initialized")
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN] = minibrew_client
    except Exception as ex:
        _LOGGER.error("Could not connect to Minibrew: %s", ex)
        raise ConfigEntryNotReady from ex

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    return unload_ok