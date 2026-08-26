import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN
from pymbrewclient import BreweryClient


_LOGGER = logging.getLogger(__name__)
_DISPLAY_TITLE = "MiniBrew Pro"
_LEGACY_TITLES = {"Minibrew", "Minibrew Pro", "MiniBrew"}

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Minibrew integration."""
    _LOGGER.debug("Setting up Minibrew integration")
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Minibrew from a config entry."""
    if config_entry.title in _LEGACY_TITLES:
        hass.config_entries.async_update_entry(config_entry, title=_DISPLAY_TITLE)
    minibrew_username = config_entry.data["username"]
    minibrew_password = config_entry.data["password"]

    try:
        minibrew_client = BreweryClient(username=minibrew_username, password=minibrew_password)
        _LOGGER.debug(f"Minibrew initialized")
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][config_entry.entry_id] = {"client": minibrew_client}
    except Exception as ex:
        _LOGGER.error("Could not connect to Minibrew: %s", ex)
        raise ConfigEntryNotReady from ex

    # Reload the entry when options change (e.g. toggling real-time updates).
    config_entry.async_on_unload(config_entry.add_update_listener(async_update_options))

    await hass.config_entries.async_forward_entry_setups(config_entry, ["sensor", "binary_sensor"])
    return True

async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the config entry when its options are updated."""
    await hass.config_entries.async_reload(config_entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop the real-time MQTT stream, if one was started.
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if store:
        coordinator = store.get("coordinator")
        if coordinator is not None and getattr(coordinator, "realtime", None) is not None:
            await coordinator.realtime.async_stop()

    unload_ok =     await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor"])
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow a device to be removed from the device registry.

    Returns True only when the device is no longer present in the API, so HA
    won't let the user delete a device that is still active.
    """
    store = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    coordinator = store.get("coordinator")
    if coordinator is None or coordinator.data is None:
        return True

    # Extract the serial number from the device identifiers.
    serial = next(
        (identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN),
        None,
    )
    if serial is None:
        return True

    # Reject removal if the serial still appears in the latest API data.
    for devices in coordinator.data.__dict__.values():
        for dev in devices:
            dev_dict = dev if isinstance(dev, dict) else getattr(dev, "__dict__", {})
            if dev_dict.get("serial_number") == serial:
                return False

    return True
