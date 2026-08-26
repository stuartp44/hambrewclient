"""Binary sensors for the MiniBrew integration."""
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .sensor import _device_to_dict

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew binary sensors from a config entry."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = store.get("coordinator")
    if coordinator is None or coordinator.data is None:
        return

    entities = []
    for devices in coordinator.data.__dict__.values():
        for device_data in devices:
            device_dict = _device_to_dict(device_data)
            serial = device_dict.get("serial_number")
            if not serial:
                continue
            entities.append(
                MiniBrewRealtimeConnectedSensor(coordinator, config_entry, device_dict)
            )

    async_add_entities(entities)


class MiniBrewRealtimeConnectedSensor(BinarySensorEntity):
    """Binary sensor that reflects the live MQTT WebSocket connection state."""

    _attr_translation_key = "realtime_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, device_dict):
        self._coordinator = coordinator
        serial = device_dict["serial_number"]
        self._attr_unique_id = f"{serial}_realtime_connected"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, serial)},
            "name": device_dict.get("title", serial),
            "manufacturer": "MiniBrew",
            "serial_number": serial,
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
        """Always available — disconnected is a valid reportable state."""
        return True

    async def async_added_to_hass(self):
        """Subscribe to coordinator updates so state tracks connection changes."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
