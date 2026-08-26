"""Binary sensors for the MiniBrew integration."""
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew binary sensors from a config entry."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = store.get("coordinator")
    if coordinator is None:
        return

    async_add_entities([MiniBrewRealtimeConnectedSensor(coordinator, config_entry)])


class MiniBrewRealtimeConnectedSensor(BinarySensorEntity):
    """Binary sensor that reflects the live MQTT WebSocket connection state."""

    _attr_translation_key = "realtime_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_realtime_connected"

    @property
    def device_info(self):
        """Attach to the MiniBrew hub device (the integration entry itself)."""
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": "MiniBrew",
            "manufacturer": "MiniBrew",
        }

    @property
    def is_on(self):
        """Return True when the MQTT WebSocket stream is connected."""
        realtime = getattr(self._coordinator, "realtime", None)
        if realtime is None:
            return False
        return realtime.connected

    @property
    def available(self):
        """Always available — even when disconnected we can report the state."""
        return True

    async def async_added_to_hass(self):
        """Subscribe to coordinator updates so state tracks connection changes."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
