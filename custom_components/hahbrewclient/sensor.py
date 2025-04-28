import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew sensors from a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    sensors = []
    brewery_overview = await coordinator.async_get_brewery_overview()

    # Iterate through all devices in all states
    for state, devices in brewery_overview:
        for device in devices:
            # Add sensors for each device
            sensors.append(MiniBrewTemperatureSensor(coordinator, device, state))
            sensors.append(MiniBrewGravitySensor(coordinator, device, state))
            sensors.append(MiniBrewOnlineStatusSensor(coordinator, device, state))

    async_add_entities(sensors)


class MiniBrewSensor(CoordinatorEntity, SensorEntity):
    """Base class for MiniBrew sensors."""

    def __init__(self, coordinator, device, state):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.device = device
        self.state = state
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial_number"])},
            "name": device["title"],
            "manufacturer": "MiniBrew",
            "model": device["device_type"],
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device['serial_number']}_{self.state}_{self.name}"


class MiniBrewTemperatureSensor(MiniBrewSensor):
    """Sensor for the current temperature of the device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self.device['title']} Temperature ({self.state})"

    @property
    def state(self):
        """Return the current temperature."""
        return self.device["current_temp"]

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"


class MiniBrewGravitySensor(MiniBrewSensor):
    """Sensor for the gravity of the device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self.device['title']} Gravity ({self.state})"

    @property
    def state(self):
        """Return the gravity."""
        return self.device["gravity"]


class MiniBrewOnlineStatusSensor(MiniBrewSensor):
    """Sensor for the online status of the device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self.device['title']} Online Status ({self.state})"

    @property
    def state(self):
        """Return the online status."""
        return "Online" if self.device["online"] else "Offline"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC