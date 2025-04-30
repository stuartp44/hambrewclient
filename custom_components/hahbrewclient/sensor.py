import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymbrewclient import BreweryOverview, Device
from datetime import timedelta

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew sensors from a config entry."""
    client = hass.data[DOMAIN][config_entry.entry_id]  # Get the BreweryClient instance
    sensors = []

    # Create a DataUpdateCoordinator
    coordinator = MiniBrewDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()


    _LOGGER.debug(f"Brewery overview: {coordinator}")
    
    # Iterate through all states and devices
    for state, devices in coordinator.data.__dict__.items():  # Access states dynamically
        _LOGGER.debug(f"State: {state}, Devices: {devices}")
        for device_data in devices:
            # Convert the raw dictionary to a Device object
            device = Device(**device_data)
            # Add sensors for MiniBrew devices
            if device.device_type == 0:  # MiniBrew device
                sensors.append(MiniBrewTemperatureSensor(coordinator, device,  state))
                sensors.append(MiniBrewOnlineStatusSensor(coordinator, device, state))
                sensors.append(MiniBrewIsUpdatingSensor(coordinator, device, state))
                sensors.append(MiniBrewBrewStageSensor(coordinator, device, state))
            # Add sensors for Keg devices
            elif device.device_type == 1:  # Keg device
                sensors.append(KegCurrentTemperatureSensor(coordinator, device, state))
                sensors.append(KegTargetTemperatureSensor(coordinator, device, state))
                sensors.append(KegBeerStyleSensor(coordinator, device, state))
                sensors.append(KegOnlineStatusSensor(coordinator,device, state))
                sensors.append(KegIsUpdatingSensor(coordinator, device, state))

    async_add_entities(sensors)

class MiniBrewDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching MiniBrew data from the API."""

    def __init__(self, hass, client):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="MiniBrew Data Update Coordinator",
            update_interval=timedelta(seconds=30),  # Fetch data every 30 seconds
        )
        self.client = client

    async def _async_update_data(self):
        """Fetch data from the API."""
        try:
            return await self.hass.async_add_executor_job(self.client.get_brewery_overview)
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}")

class MiniBrewSensor(SensorEntity):
    """Base class for MiniBrew sensors."""

    def __init__(self, coordinator, device: Device, state: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
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
    
    @property
    def available(self):
        """Return if the sensor is available."""
        return self.coordinator.last_update_success

    async def async_update(self):
        """Update the sensor."""
        await self.coordinator.async_request_refresh()

    @property
    def should_poll(self):
        """Disable polling, updates are handled by the coordinator."""
        return False

    async def async_added_to_hass(self):
        """Register callbacks."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class MiniBrewIsUpdatingSensor(MiniBrewSensor):
    """Sensor for the update status of the MiniBrew device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def state(self):
        """Return the update status."""
        return "Updating" if self.device.is_updating else "Not Updating"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class MiniBrewCurrentStageSensor(MiniBrewSensor):
    """Sensor for the current stage of the MiniBrew device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    def state(self):
        """Return the current stage."""
        return self.device.current_stage

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class KegSensor(SensorEntity):
    """Base class for Keg sensors."""

    def __init__(self, coordinator, device: Device, state: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
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
    
    @property
    def available(self):
        """Return if the sensor is available."""
        return self.coordinator.last_update_success

    async def async_update(self):
        """Update the sensor."""
        await self.coordinator.async_request_refresh()

    @property
    def should_poll(self):
        """Disable polling, updates are handled by the coordinator."""
        return False

    async def async_added_to_hass(self):
        """Register callbacks."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

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

    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

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
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class KegIsUpdatingSensor(KegSensor):
    """Sensor for the update status of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def state(self):
        """Return the update status."""
        return "Updating" if self.device.is_updating else "Not Updating"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC
    
    @property
    def identify(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"