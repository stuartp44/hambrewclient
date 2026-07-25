import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymbrewclient import Device

from .const import (
    CONF_ENABLE_REALTIME,
    CONF_REALTIME_POLL_INTERVAL,
    CONF_REFRESH_INTERVAL,
    DEFAULT_ENABLE_REALTIME,
    DEFAULT_REALTIME_POLL_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)
from .realtime import MiniBrewRealtimeManager, overlay_mqtt

_LOGGER = logging.getLogger(__name__)


def _device_to_dict(device):
    if isinstance(device, dict):
        return device
    if isinstance(device, Device):
        if is_dataclass(device):
            return asdict(device)
        return device.__dict__
    if hasattr(device, "__dict__"):
        return device.__dict__
    return {}


def _coerce_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _overlay_mqtt(device: dict, telemetry) -> None:
    """Overlay live MQTT telemetry onto a REST device dict, in place.

    Thin re-export of :func:`realtime.overlay_mqtt`; kept for readability at the
    call site in the coordinator.
    """
    overlay_mqtt(device, telemetry)


def _collect_serials(coordinator, data=None):
    """Return the set of device serial numbers present in the overview."""
    overview = data if data is not None else coordinator.data
    serials = set()
    if overview is None:
        return serials
    for devices in overview.__dict__.values():
        for device_data in devices:
            serial = _device_to_dict(device_data).get("serial_number")
            if serial:
                serials.add(serial)
    return serials


def _format_duration_seconds(value):
    if value is None:
        return None

    try:
        total_seconds = max(0, int(value))
    except (TypeError, ValueError):
        return None

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew sensors from a config entry."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    client = store["client"]  # Get the BreweryClient instance
    added_devices = set()

    # Create a DataUpdateCoordinator
    coordinator = MiniBrewDataUpdateCoordinator(hass, client, config_entry)
    await coordinator.async_config_entry_first_refresh()

    # Expose the coordinator for unload (so the MQTT stream can be stopped).
    store["coordinator"] = coordinator

    # Start the optional real-time MQTT stream and subscribe to known devices.
    if coordinator.realtime_enabled:
        manager = MiniBrewRealtimeManager(hass, coordinator, client)
        await manager.async_start()
        coordinator.realtime = manager
        manager.async_ensure_subscribed(_collect_serials(coordinator))

    _LOGGER.debug(f"Brewery overview: {coordinator}")
    
    # Function to add new sensors dynamically
    def add_new_sensors():
        # Track devices currently in the API response
        current_devices = set()
        new_sensors = []
        
        for state, devices in coordinator.data.__dict__.items():  # Access states dynamically
            for device_data in devices:
                device_dict = _device_to_dict(device_data)
                serial_number = device_dict.get("serial_number")
                if not serial_number:
                    continue
                
                current_devices.add(serial_number)
                
                # Check if the device has already been added
                if serial_number in added_devices:
                    continue

                # Convert the raw dictionary to a Device object
                device = device_data if isinstance(device_data, Device) else Device(**device_dict)

                # Add sensors for MiniBrew devices
                if device.device_type == 0:  # Craft device
                    new_sensors.append(CraftSensorCurrentTemperatureSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorTargetTemperatureSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorOnlineStatusSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorIsUpdatingSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorBrewStageSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorTimeInStageSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorCurrentStageSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorNeedsCleaningSensor(coordinator, device, state))
                    new_sensors.append(CraftUserActionRequiredSensor(coordinator, device, state))
                    new_sensors.append(CraftNextActionDateTimeSensor(coordinator, device, state))
                    if coordinator.realtime_enabled:
                        new_sensors.append(CraftWifiSignalSensor(coordinator, device, state))
                # Add sensors for Keg devices
                elif device.device_type == 1:  # Keg device
                    new_sensors.append(KegCurrentTemperatureSensor(coordinator, device, state))
                    new_sensors.append(KegTargetTemperatureSensor(coordinator, device, state))
                    new_sensors.append(KegBeerStyleSensor(coordinator, device, state))
                    new_sensors.append(KegBeerNameSensor(coordinator, device, state))
                    new_sensors.append(KegTimeInStageSensor(coordinator, device, state))
                    new_sensors.append(KegOnlineStatusSensor(coordinator, device, state))
                    new_sensors.append(KegIsUpdatingSensor(coordinator, device, state))
                    new_sensors.append(KegNeedsCleaningSensor(coordinator, device, state))
                    new_sensors.append(KegActionRequiredSensor(coordinator, device, state))
                    new_sensors.append(KegNextActionDateTimeSensor(coordinator, device, state))
                    if coordinator.realtime_enabled:
                        new_sensors.append(KegWifiSignalSensor(coordinator, device, state))
                # Mark the device as added
                added_devices.add(serial_number)

        # Remove devices that are no longer in the API response
        # This allows offline/reconnecting devices to be re-registered
        added_devices.intersection_update(current_devices)

        return new_sensors

    # Add initial sensors
    async_add_entities(add_new_sensors())

    # Listen for updates from the coordinator and add any newly discovered
    # devices. This fires on every coordinator update — including frequent
    # real-time MQTT telemetry — so only newly created entities are added.
    def handle_coordinator_update():
        new_sensors = add_new_sensors()
        if new_sensors:
            async_add_entities(new_sensors)

    coordinator.async_add_listener(handle_coordinator_update)

class MiniBrewDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching MiniBrew data from the API."""

    def __init__(self, hass, client, config_entry):
        """Initialize the coordinator."""
        self.client = client
        self.config_entry = config_entry

        options = config_entry.options
        self.realtime_enabled = options.get(CONF_ENABLE_REALTIME, DEFAULT_ENABLE_REALTIME)
        refresh_interval = options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
        realtime_poll_interval = options.get(
            CONF_REALTIME_POLL_INTERVAL, DEFAULT_REALTIME_POLL_INTERVAL
        )

        # When real-time is on, MQTT drives the fast fields, so poll slowly
        # (discovery + slow fields only); otherwise poll at the normal interval.
        poll_interval = realtime_poll_interval if self.realtime_enabled else refresh_interval

        # Set by async_setup_entry once the MQTT stream is started.
        self.realtime = None

        super().__init__(
            hass,
            _LOGGER,
            name="MiniBrew Data Update Coordinator",
            update_interval=timedelta(seconds=poll_interval),
        )

    async def _async_update_data(self):
        """Fetch data from the API."""
        try:
            _LOGGER.debug("Fetching data from MiniBrew API...")
            data = await self.hass.async_add_executor_job(self.client.get_brewery_overview)
            _LOGGER.debug(f"Fetched data: {data}")
        except Exception as err:
            _LOGGER.error(f"Error fetching data: {err}")
            raise UpdateFailed(f"Error fetching data: {err}")

        # Subscribe the MQTT stream to any newly discovered devices.
        if self.realtime is not None:
            self.realtime.async_ensure_subscribed(_collect_serials(self, data))

        return data

    def get_telemetry(self, serial):
        """Return the latest MQTT telemetry for a serial, or ``None``."""
        if self.realtime is None:
            return None
        return self.realtime.get_telemetry(serial)

    @property
    def realtime_connected(self):
        """Return whether the real-time MQTT stream is connected."""
        return self.realtime is not None and self.realtime.connected

    def get_merged_device(self, serial, state):
        """Return the REST device dict for a serial, overlaid with live telemetry.

        Returns ``None`` when the device is not present in the given state group.
        """
        devices = getattr(self.data, state, [])
        for dev in devices:
            device_dict = _device_to_dict(dev)
            if device_dict.get("serial_number") == serial:
                merged = dict(device_dict)
                if self.realtime_enabled:
                    _overlay_mqtt(merged, self.get_telemetry(serial))
                return merged
        return None

class CraftSensor(SensorEntity):
    """Base class for MiniBrew sensors."""

    def __init__(self, coordinator, device: Device, state: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.device = device
        self.device_id = device.serial_number 
        self.device_type = state
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.serial_number)},
            "name": device.title,
            "manufacturer": "MiniBrew",
            "model": "Craft",
            "sw_version": device.software_version,
            "serial_number": device.serial_number,
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
        """Get the latest device data (REST overlaid with live telemetry)."""
        return self.coordinator.get_merged_device(self.device_id, self.device_type)

class CraftSensorBrewStageSensor(CraftSensor):
    """Sensor for the current brew stage of the Craft device."""

    _attr_translation_key = "brew_stage"

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

    _attr_translation_key = "current_temperature"

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

    _attr_translation_key = "target_temperature"

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

    _attr_translation_key = "cloud_connection"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def native_value(self):
        """Return the online status."""
        device = self._get_latest_device()
        return "online" if device and device.get("online") else "offline"

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

    _attr_translation_key = "update_status"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        return "updating" if device and device.get("updating") else "not_updating"

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

    _attr_translation_key = "user_action_required"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    def native_value(self):
        """Return the user action required status."""
        device = self._get_latest_device()
        if not device:
            return "unknown"

        action = device.get("user_action")

        if action != 0 and action is not None:
            return "action_required"
        elif action == 0:
            return "no_action_required"
        return "unknown"


    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        device = self._get_latest_device()
        action = device.get("user_action") if device else None
        
        if action != 0 and action is not None:
            return "mdi:alert"
        else:
            return "mdi:check-circle"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_user_action_required"


class CraftNextActionDateTimeSensor(CraftSensor):
    """Sensor for the actual timestamp of the next required action."""

    _attr_translation_key = "next_action_time_remaining"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the actual next-action timestamp (MQTT when realtime, else REST)."""
        device = self._get_latest_device()
        if not device:
            return None
        return _coerce_timestamp(device.get("process_estimate_remaining"))

    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:calendar-clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_next_action_time_remaining"


class CraftSensorCurrentStageSensor(CraftSensor):
    """Sensor for the current stage of the Craft device."""

    _attr_translation_key = "current_stage"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    def native_value(self):
        """Return a human-readable phase name based on the device's group."""
        for group_name, devices in self.coordinator.data.__dict__.items():
            for dev in devices:
                device_dict = _device_to_dict(dev)
                if device_dict.get("serial_number") == self.device_id:
                    return group_name

        return "unknown"

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_current_stage"

class CraftSensorTimeInStageSensor(CraftSensor):
    """Sensor for the formatted time spent in the current stage of the Craft device."""

    _attr_translation_key = "time_in_stage"
    _attr_native_unit_of_measurement = None
    _attr_suggested_unit_of_measurement = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Time in Stage"

    @property
    def native_value(self):
        """Return a human-readable duration for the time spent in the current stage."""
        device = self._get_latest_device()
        if not device:
            return None

        return _format_duration_seconds(device.get("status_time"))

    @property
    def unit_of_measurement(self):
        """Force no unit to avoid HA appending legacy seconds metadata."""
        return None

    @property
    def available(self):
        """Return True if the sensor has data."""
        device = self._get_latest_device()
        return device is not None and device.get("status_time") is not None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:clock"

    @property
    def extra_state_attributes(self):
        """Expose raw and formatted values for runtime verification."""
        device = self._get_latest_device()
        raw_seconds = device.get("status_time") if device else None
        return {
            "raw_status_time_seconds": raw_seconds,
            "formatted_status_time": _format_duration_seconds(raw_seconds),
            "format_version": "hms-v2",
        }

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_time_in_stage"

class CraftSensorNeedsCleaningSensor(CraftSensor):
    """Sensor for the cleaning status of the Craft device."""

    _attr_translation_key = "needs_cleaning"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        return "needs_cleaning" if device and device.get("needs_acid_cleaning") else "clean"

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
        self.device = device
        self.device_id = device.serial_number
        self.device_type = state
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.serial_number)},
            "name": device.title,
            "manufacturer": "MiniBrew",
            "model": "Smart Keg",
            "sw_version": device.software_version,
            "serial_number": device.serial_number,
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device_id}_{self.name}"
    
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
        """Get the latest device data (REST overlaid with live telemetry)."""
        return self.coordinator.get_merged_device(self.device_id, self.device_type)

class KegCurrentTemperatureSensor(KegSensor):
    """Sensor for the current temperature of the Keg device."""

    _attr_translation_key = "temperature"

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
        return f"{self.device_id}_{self.name}"

class KegTargetTemperatureSensor(KegSensor):
    """Sensor for the target temperature of the Keg device."""

    _attr_translation_key = "target_temperature"

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
        return f"{self.device_id}_{self.name}"

class KegBeerStyleSensor(KegSensor):
    """Sensor for the beer style of the Keg device."""

    _attr_translation_key = "beer_style"

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
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_{self.name}"


class KegBeerNameSensor(KegSensor):
    """Sensor for the beer name of the Keg device."""

    _attr_translation_key = "beer_name"

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
        return f"{self.device_id}_{self.name}"


class KegTimeInStageSensor(KegSensor):
    """Sensor for the formatted time spent in the current stage of the Keg device."""

    _attr_translation_key = "time_in_stage"
    _attr_native_unit_of_measurement = None
    _attr_suggested_unit_of_measurement = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Time in Stage"

    @property
    def native_value(self):
        """Return a human-readable duration for the time spent in the current stage."""
        device = self._get_latest_device()
        if not device:
            return None

        return _format_duration_seconds(device.get("status_time"))

    @property
    def unit_of_measurement(self):
        """Force no unit to avoid HA appending legacy seconds metadata."""
        return None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:clock-time-eight"

    @property
    def extra_state_attributes(self):
        """Expose raw and formatted values for runtime verification."""
        device = self._get_latest_device()
        raw_seconds = device.get("status_time") if device else None
        return {
            "raw_status_time_seconds": raw_seconds,
            "formatted_status_time": _format_duration_seconds(raw_seconds),
            "format_version": "hms-v2",
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        device = self._get_latest_device()
        return device is not None and device.get("status_time") is not None

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_time_in_stage"


class KegOnlineStatusSensor(KegSensor):
    """Sensor for the online status of the Keg device."""

    _attr_translation_key = "cloud_connection"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    def native_value(self):
        """Return the online status."""
        device = self._get_latest_device()
        return "online" if device and device.get("online") else "offline"

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
        return f"{self.device_id}_{self.name}"


class KegIsUpdatingSensor(KegSensor):
    """Sensor for the update status of the Keg device."""

    _attr_translation_key = "update_status"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        return "updating" if device and device.get("updating") else "not_updating"

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
        return f"{self.device_id}_{self.name}"


class KegNeedsCleaningSensor(KegSensor):
    """Sensor for the cleaning status of the Keg device."""

    _attr_translation_key = "needs_cleaning"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        return "needs_cleaning" if device and device.get("needs_acid_cleaning") else "clean"

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
        return f"{self.device_id}_{self.name}"

class KegActionRequiredSensor(KegSensor):
    """Sensor for user action required status of the Keg device."""

    _attr_translation_key = "user_action_required"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    def native_value(self):
        """Return the user action required status."""
        device = self._get_latest_device()
        if not device:
            return "unknown"

        action = device.get("user_action")

        if action != 0 and action is not None:
            return "action_required"
        elif action == 0:
            return "no_action_required"
        return "unknown"


    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        device = self._get_latest_device()
        action = device.get("user_action") if device else None
        
        if action != 0 and action is not None:
            return "mdi:alert"
        else:
            return "mdi:check-circle"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_{self.name}"


class KegNextActionDateTimeSensor(KegSensor):
    """Sensor for the actual timestamp of the next required action."""

    _attr_translation_key = "next_action_time_remaining"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Return the actual next-action timestamp (MQTT when realtime, else REST)."""
        device = self._get_latest_device()
        if not device:
            return None
        return _coerce_timestamp(device.get("process_estimate_remaining"))

    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:calendar-clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_next_action_time_remaining"


class CraftWifiSignalSensor(CraftSensor):
    """Sensor for the Wi-Fi signal strength of the Craft device (MQTT only)."""

    _attr_translation_key = "wifi_signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the Wi-Fi RSSI in dBm from the latest telemetry."""
        telemetry = self.coordinator.get_telemetry(self.device_id)
        return telemetry.wifi_rssi_dbm if telemetry else None

    @property
    def available(self):
        """Return True once real-time telemetry with an RSSI has arrived."""
        telemetry = self.coordinator.get_telemetry(self.device_id)
        return telemetry is not None and telemetry.wifi_rssi_dbm is not None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:wifi"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_wifi_signal"


class KegWifiSignalSensor(KegSensor):
    """Sensor for the Wi-Fi signal strength of the Keg device (MQTT only)."""

    _attr_translation_key = "wifi_signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the Wi-Fi RSSI in dBm from the latest telemetry."""
        telemetry = self.coordinator.get_telemetry(self.device_id)
        return telemetry.wifi_rssi_dbm if telemetry else None

    @property
    def available(self):
        """Return True once real-time telemetry with an RSSI has arrived."""
        telemetry = self.coordinator.get_telemetry(self.device_id)
        return telemetry is not None and telemetry.wifi_rssi_dbm is not None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:wifi"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_wifi_signal"
