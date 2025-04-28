import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from pymbrewclient import BreweryOverview, Device

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew sensors from a config entry."""
    client = hass.data[DOMAIN][config_entry.entry_id]  # Get the BreweryClient instance
    sensors = []

    # Fetch the brewery overview directly from the client
    brewery_overview: BreweryOverview = await hass.async_add_executor_job(client.get_brewery_overview)
    _LOGGER.debug(f"Brewery overview: {brewery_overview}")
    
    # Iterate through all states and devices
    for state, devices in brewery_overview.__dict__.items():  # Access states dynamically
        _LOGGER.debug(f"State: {state}, Devices: {devices}")
        for device_data in devices:
            # Convert the raw dictionary to a Device object
            device = Device(**device_data)
            # Add sensors for MiniBrew devices
            if device.device_type == 0:  # MiniBrew device
                sensors.append(MiniBrewTemperatureSensor(device, state))
                sensors.append(MiniBrewOnlineStatusSensor(device, state))
            # Add sensors for Keg devices
            elif device.device_type == 1:  # Keg device
                sensors.append(KegCurrentTemperatureSensor(device, state))
                sensors.append(KegTargetTemperatureSensor(device, state))
                sensors.append(KegBeerStyleSensor(device, state))
                sensors.append(KegOnlineStatusSensor(device, state))

    async_add_entities(sensors)

class MiniBrewSensor(SensorEntity):
    """Base class for MiniBrew sensors."""

    def __init__(self, device: Device, state: str):
        """Initialize the sensor."""
        self.device = device
        self._state = state 
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.serial_number)},
            "name": device.title,
            "manufacturer": "MiniBrew",
            "model": "Craft",
            "sw_version": device.software_version,
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class MiniBrewBrewStageSensor(MiniBrewSensor):
    """Sensor for the current brew stage of the MiniBrew device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Brew Stage"

    @property
    def state(self):
        """Return the current brew stage."""
        return self.device.brew_stage

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

class MiniBrewTemperatureSensor(MiniBrewSensor):
    """Sensor for the current temperature of the MiniBrew device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Temperature"

    @property
    def state(self):
        """Return the current temperature."""
        return self.device.current_temp

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

class MiniBrewOnlineStatusSensor(MiniBrewSensor):
    """Sensor for the online status of the MiniBrew device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def state(self):
        """Return the online status."""
        return "Online" if self.device.online else "Offline"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC


class KegSensor(SensorEntity):
    """Base class for Keg sensors."""

    def __init__(self, device: Device, state: str):
        """Initialize the sensor."""
        self.device = device
        self._state = state
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.serial_number)},
            "name": device.title,
            "manufacturer": "MiniBrew",
            "model": "Smart Keg",
            "sw_version": device.software_version,
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device.serial_number}_{self.name}"


class KegCurrentTemperatureSensor(KegSensor):
    """Sensor for the current temperature of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Temperature"

    @property
    def state(self):
        """Return the current temperature."""
        return self.device.current_temp

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

class KegTargetTemperatureSensor(KegSensor):
    """Sensor for the Target temperature of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Temperature"

    @property
    def state(self):
        """Return the current temperature."""
        return self.device.target_temp

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"


class KegBeerStyleSensor(KegSensor):
    """Sensor for the beer style of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Beer Style"

    @property
    def state(self):
        """Return the beer style."""
        return self.device.beer_style


class KegOnlineStatusSensor(KegSensor):
    """Sensor for the online status of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def state(self):
        """Return the online status."""
        return "Online" if self.device.online else "Offline"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC