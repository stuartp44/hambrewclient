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
            if device.device_type == 0:  # Craft device
                sensors.append(CraftSensorCurrentTemperatureSensor(coordinator, device,  state))
                sensors.append(CraftSensorTargetTemperatureSensor(coordinator, device, state))
                sensors.append(CraftSensorOnlineStatusSensor(coordinator, device, state))
                sensors.append(CraftSensorIsUpdatingSensor(coordinator, device, state))
                sensors.append(CraftSensorBrewStageSensor(coordinator, device, state))
                sensors.append(CraftSensorTimeInStageSensor(coordinator, device, state))
                sensors.append(CraftSensorCurrentStageSensor(coordinator, device, state))
                sensors.append(CraftSensorNeedsCleaningSensor(coordinator, device, state))
            # Add sensors for Keg devices
            elif device.device_type == 1:  # Keg device
                sensors.append(KegCurrentTemperatureSensor(coordinator, device, state))
                sensors.append(KegTargetTemperatureSensor(coordinator, device, state))
                sensors.append(KegBeerStyleSensor(coordinator, device, state))
                sensors.append(KegOnlineStatusSensor(coordinator,device, state))
                sensors.append(KegIsUpdatingSensor(coordinator, device, state))
                sensors.append(KegNeedsCleaningSensor(coordinator, device, state))
                

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
            _LOGGER.debug("Fetching data from MiniBrew API...")
            data = await self.hass.async_add_executor_job(self.client.get_brewery_overview)
            _LOGGER.debug(f"Fetched data: {data}")
            return data
        except Exception as err:
            _LOGGER.error(f"Error fetching data: {err}")
            raise UpdateFailed(f"Error fetching data: {err}")

class CraftSensor(SensorEntity):
    """Base class for MiniBrew sensors."""

    def __init__(self, coordinator, device: Device, state: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.device_id = device.serial_number 
        self.device_type = state
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

    def _get_latest_device(self):
        """Get the latest device data from the coordinator."""
        devices = getattr(self.coordinator.data, self.device_type, [])
        for dev in devices:
            if dev["serial_number"] == self.device_id:
                return dev
        return None

class CraftSensorBrewStageSensor(CraftSensor):
    """Sensor for the current brew stage of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Brew Stage"

    @property
    def native_value(self):
        """Return the current brew stage."""
        device = self._get_latest_device()
        return device.get("stage") if device else None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:routes-clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_brew_stage"


class CraftSensorCurrentTemperatureSensor(CraftSensor):
    """Sensor for the current temperature of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Temperature"

    @property
    def native_value(self):
        """Return the current temperature."""
        device = self._get_latest_device()
        return device.get("current_temp") if device else None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermometer"

    @property
    def available(self):
        """Return True if the sensor has data."""
        device = self._get_latest_device()
        return device is not None and device.get("current_temp") is not None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_current_temperature"


class CraftSensorTargetTemperatureSensor(CraftSensor):
    """Sensor for the target temperature of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Target Temperature"

    @property
    def native_value(self):
        """Return the target temperature."""
        device = self._get_latest_device()
        return device.get("target_temp") if device else None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermometer"

    @property
    def available(self):
        """Return True if the sensor has data."""
        device = self._get_latest_device()
        return device is not None and device.get("target_temp") is not None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_target_temperature"


class CraftSensorOnlineStatusSensor(CraftSensor):
    """Sensor for the online status of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def native_value(self):
        """Return the online status."""
        device = self._get_latest_device()
        return "Online" if device and device.get("online") else "Offline"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:cloud-check"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_online_status"


class CraftSensorIsUpdatingSensor(CraftSensor):
    """Sensor for the update status of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        return "Updating" if device and device.get("updating") else "Not Updating"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:cloud-sync"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_is_updating"

class CraftUserActionRequiredSensor(CraftSensor):
    """Sensor for user action required status of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    def native_value(self):
        """Return the user action required status."""
        device = self._get_latest_device()
        if not device:
            return "Unknown"

        action = device.get("user_action")

        if action == 2:
            return "Action Required"
        elif action == 0:
            return "No Action Required"
        return "Unknown"


    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        device = self._get_latest_device()
        action = device.get("user_action") if device else None
        
        if action == 2:
            return "mdi:alert"
        else:
            return "mdi:check-circle"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_user_action_required"


class CraftSensorCurrentStageSensor(CraftSensor):
    """Sensor for the current stage of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    def native_value(self):
        """Return a human-readable phase name based on the device's group."""
        phase_map = {
            "brew_clean_idle": "Clean and Ready to Brew",
            "fermenting": "Fermenting",
            "serving": "Serving",
            "brew_acid_clean_idle": "Ready to Clean"
        }

        for group_name, devices in self.coordinator.data.__dict__.items():
            for dev in devices:
                if dev.get("serial_number") == self.device_id:
                    return phase_map.get(group_name, group_name)  # fallback to raw name

        return "Unknown"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_current_stage"

class CraftSensorTimeInStageSensor(CraftSensor):
    """Sensor for the time spent in the current stage of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Time in Stage"

    @property
    def native_value(self):
        """Return the time spent in the current stage."""
        device = self._get_latest_device()
        return device.get("status_time") if device else None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "seconds"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_time_in_stage"

class CraftSensorNeedsCleaningSensor(CraftSensor):
    """Sensor for the cleaning status of the Craft device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        return "Needs Cleaning" if device and device.get("needs_acid_cleaning") else "Clean"

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:broom"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_needs_cleaning"

class KegSensor(SensorEntity):
    """Base class for Keg sensors."""

    def __init__(self, coordinator, device: Device, state: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.device_id = device.serial_number
        self.device_type = state
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

    def _get_latest_device(self):
        """Get the latest device data from the coordinator."""
        devices = getattr(self.coordinator.data, self.device_type, [])
        for dev in devices:
            if dev["serial_number"] == self.device_id:
                return dev
        return None

class KegCurrentTemperatureSensor(KegSensor):
    """Sensor for the current temperature of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Temperature"

    @property
    def native_value(self):
        """Return the current temperature."""
        device = self._get_latest_device()
        return device.get("current_temp") if device else None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermometer"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        device = self._get_latest_device()
        return device is not None and device.get("current_temp") is not None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class KegTargetTemperatureSensor(KegSensor):
    """Sensor for the target temperature of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Target Temperature"

    @property
    def native_value(self):
        """Return the target temperature."""
        device = self._get_latest_device()
        return device.get("target_temp") if device else None

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°C"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermometer"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        device = self._get_latest_device()
        return device is not None and device.get("target_temp") is not None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"

class KegBeerStyleSensor(KegSensor):
    """Sensor for the beer style of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Beer Style"

    @property
    def native_value(self):
        """Return the beer style."""
        device = self._get_latest_device()
        return device.get("beer_style") if device else None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"


class KegBeerNameSensor(KegSensor):
    """Sensor for the beer name of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Beer Name"

    @property
    def native_value(self):
        """Return the beer name."""
        device = self._get_latest_device()
        return device.get("beer_name") or "N/A" if device else "N/A"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer-outline"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"


class KegOnlineStatusSensor(KegSensor):
    """Sensor for the online status of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def native_value(self):
        """Return the online status."""
        device = self._get_latest_device()
        return "Online" if device and device.get("online") else "Offline"

    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:cloud-check"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"


class KegIsUpdatingSensor(KegSensor):
    """Sensor for the update status of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        return "Updating" if device and device.get("updating") else "Not Updating"

    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:cloud-sync"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"


class KegNeedsCleaningSensor(KegSensor):
    """Sensor for the cleaning status of the Keg device."""

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        return "Needs Cleaning" if device and device.get("needs_acid_cleaning") else "Clean"

    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:broom"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device.serial_number}_{self.name}"