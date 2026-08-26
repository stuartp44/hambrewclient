"""Binary sensors for the MiniBrew integration."""
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from .const import DOMAIN
from .sensor import _device_to_dict

_LOGGER = logging.getLogger(__name__)


def _disable_realtime_connected_entities(hass, config_entry):
    """Disable the realtime_connected binary sensor by default via the registry.

    Mirrors the pattern used for ESP32 core temperature: the entity registers
    enabled, then the integration immediately disables it so it shows in the
    entity list (greyed out) but doesn't clutter the device card by default.
    """
    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if not entry.unique_id.endswith("_realtime_connected"):
            continue
        if entry.disabled_by == RegistryEntryDisabler.INTEGRATION:
            continue
        registry.async_update_entity(
            entry.entity_id,
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )


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
    _disable_realtime_connected_entities(hass, config_entry)


class MiniBrewRealtimeConnectedSensor(BinarySensorEntity):
    """Binary sensor that reflects the live MQTT WebSocket connection state."""

    _attr_translation_key = "realtime_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
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
