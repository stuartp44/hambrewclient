import logging
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymbrewclient import Device, ProcessPhase, ProcessType, SensorType
from pymbrewclient.mqtt.enums import UserAction

from .const import (
    CONF_REALTIME_POLL_INTERVAL,
    DEFAULT_REALTIME_POLL_INTERVAL,
    DOMAIN,
)
from .realtime import MiniBrewRealtimeManager, overlay_mqtt

_LOGGER = logging.getLogger(__name__)

_USER_ACTION_REQUIRED_OPTIONS = [
    "Action required",
    "No action required",
    "Unknown",
]

_CLOUD_CONNECTION_OPTIONS = [
    "Online",
    "Offline",
]

_UPDATE_STATUS_OPTIONS = [
    "Updating",
    "Not updating",
]

_BUTTON_OPTIONS = [
    "On",
    "Off",
]

_PELTIER_MODE_OPTIONS = [
    "Cooling",
    "Warming",
    "Idle",
]

_CURRENT_STAGE_OPTIONS = [
    "Clean and ready to brew",
    "Fermenting",
    "Serving",
    "Ready to clean",
    "Unknown",
]

_NEEDS_CLEANING_OPTIONS = [
    "Needs cleaning",
    "Clean",
]


def _display_option(value, mapping):
    """Return a human-friendly label for a raw sensor option."""
    if value is None:
        return None
    return mapping.get(value, value)


def _build_process_phase_options() -> list[str]:
    """Build stable display options from ProcessPhase enum names."""
    options: list[str] = []
    for member in ProcessPhase:
        phase_name = member.name.lower()
        if phase_name == "phase_none":
            label = "None"
        else:
            for prefix in ("brew_", "ferm_", "serv_", "clean_mb_", "acid_clean_mb_"):
                if phase_name.startswith(prefix):
                    phase_name = phase_name[len(prefix):]
                    break
            label = phase_name.replace("_", " ").title()
        if label not in options:
            options.append(label)
    return options


_PROCESS_PHASE_OPTIONS = _build_process_phase_options()


def _device_to_dict(device):
    if isinstance(device, dict):
        return device
    if isinstance(device, Device):
        if is_dataclass(device):
            return {field.name: getattr(device, field.name) for field in fields(device)}
        return device.__dict__
    if hasattr(device, "__dict__"):
        return device.__dict__
    return {}


def _device_serial(device):
    """Read a serial without converting the entire device payload."""
    if isinstance(device, dict):
        return device.get("serial_number")
    return getattr(device, "serial_number", None)


def _firmware_version(version: str | None) -> str | None:
    """Hide the ESP-IDF build suffix in device information."""
    if version is None:
        return None
    firmware, separator, build = version.partition(",")
    if separator and build.strip().startswith("idf-"):
        return firmware.strip()
    return version


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


def _coerce_session_id(value):
    """Return a normalized integer session ID or ``None``."""
    if value in (None, ""):
        return None
    try:
        session_id = int(value)
    except (TypeError, ValueError):
        return None
    if session_id <= 0:
        return None
    return session_id

def _session_id_from_device_dict(device_dict):
    """Return (session_id, key_present) from API device payload fields."""
    if not isinstance(device_dict, dict):
        return None, False
    if "active_session" in device_dict:
        return _coerce_session_id(device_dict.get("active_session")), True
    if "session_id" in device_dict:
        return _coerce_session_id(device_dict.get("session_id")), True
    return None, False



def _coerce_epoch_timestamp(value):
    """Return timezone-aware UTC datetime parsed from numeric epoch seconds."""
    if value in (None, ""):
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _session_started_from_session(session):
    """Return best-effort session start timestamp from a Session payload."""
    if isinstance(session, dict):
        created = _coerce_timestamp(session.get("created"))
        if created is not None:
            return created

        for attr in ("brew_timestamp", "timestamp_original_gravity"):
            fallback = _coerce_epoch_timestamp(session.get(attr))
            if fallback is not None:
                return fallback
        return None

    created = _coerce_timestamp(getattr(session, "created", None))
    if created is not None:
        return created

    # Fallback for payloads that omit "created" but include numeric epoch fields.
    for attr in ("brew_timestamp", "timestamp_original_gravity"):
        fallback = _coerce_epoch_timestamp(getattr(session, attr, None))
        if fallback is not None:
            return fallback

    return None


def _next_action_state(value):
    """Return the current next-action timestamp truncated to the minute."""
    ts = _coerce_timestamp(value)
    if ts is None:
        return None
    return ts.replace(second=0, microsecond=0)


def _sensor_available(entity):
    """Check the source needed by this entity, not whether its value is set."""
    coordinator = entity.coordinator
    rest_available = (
        coordinator.last_update_success
        and coordinator.get_rest_device(entity.device_id) is not None
    )
    if entity._data_source == "rest":
        return rest_available
    if entity._data_source == "rest_device":
        device = coordinator.get_rest_device(entity.device_id)
        return rest_available and device.get("online") is True
    telemetry_available = coordinator.get_telemetry(entity.device_id) is not None
    if entity._data_source == "mqtt":
        return telemetry_available
    if entity._data_source == "connection":
        return rest_available or telemetry_available
    return coordinator.is_device_online(entity.device_id)


def _cloud_connection_state(coordinator, serial):
    device = coordinator.get_rest_device(serial)
    online = device.get("online") if device and coordinator.last_update_success else None
    if online is False:
        return "Offline"
    if coordinator.get_telemetry(serial) is not None:
        return "Online"
    if not isinstance(online, bool):
        return None
    return "Online" if online else "Offline"

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
            serial = _device_serial(device_data)
            if serial:
                serials.add(serial)
    return serials


def _mqtt_serials(data):
    """Require REST to confirm both connectivity and a known active process."""
    if data is None:
        return set()
    serials = set()
    for devices in data.__dict__.values():
        for device in devices:
            payload = _device_to_dict(device)
            if payload.get("online") is not True:
                continue
            try:
                process = ProcessType(payload.get("process_type"))
            except (TypeError, ValueError):
                continue
            serial = _device_serial(device)
            if serial and process != ProcessType.PROC_IDLE:
                serials.add(serial)
    return serials


def _is_auth_error(err: Exception) -> bool:
    """Return True when an exception indicates invalid credentials."""
    response = getattr(err, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in (401, 403):
        return True
    message = str(err).lower()
    return (
        "401" in message
        or "403" in message
        or "unauthorized" in message
        or "forbidden" in message
    )


def _merge_device_status_from_devices(overview, devices):
    """Merge device status from /v1/devices into the discovery overview by serial."""
    if overview is None or not devices:
        return

    status_fields = (
        "last_time_online",
        "current_state",
        "process_type",
        "process_state",
        "user_action",
        "last_process_state_change",
        "process_estimate_remaining",
        "text",
    )
    status_by_serial = {}
    for device in devices:
        device_dict = _device_to_dict(device)
        serial = device_dict.get("serial_number")
        if not serial:
            continue
        status_by_serial[serial] = {
            key: device_dict[key]
            for key in status_fields
            if key in device_dict
            and (device_dict[key] is not None or key == "process_estimate_remaining")
        }

    if not status_by_serial:
        return

    for grouped_devices in overview.__dict__.values():
        for device in grouped_devices:
            device_dict = _device_to_dict(device)
            serial = device_dict.get("serial_number")
            if not serial:
                continue
            for key, value in status_by_serial.get(serial, {}).items():
                if isinstance(device, dict):
                    device[key] = value
                else:
                    setattr(device, key, value)


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


def _telemetry_sensor_value(coordinator, serial, sensor_type: SensorType):
    """Return a typed measurement from the latest telemetry for a given serial."""
    telemetry = coordinator.get_telemetry(serial)
    if telemetry is None:
        return None

    value = None
    sensor_fn = getattr(telemetry, "sensor", None)
    if callable(sensor_fn):
        value = sensor_fn(sensor_type)
    if value is None:
        measurements = getattr(telemetry, "measurements", None)
        if isinstance(measurements, dict):
            for key in (
                sensor_type,
                getattr(sensor_type, "name", None),
                str(sensor_type),
                getattr(sensor_type, "value", None),
                str(getattr(sensor_type, "value", "")),
                getattr(sensor_type, "name", "").lower(),
                str(sensor_type).lower(),
            ):
                if key in measurements:
                    value = measurements[key]
                    break
    return value


def _telemetry_temp_control_power(coordinator, serial):
    """Return signed Peltier power from telemetry, or ``None`` when unavailable."""
    telemetry = coordinator.get_telemetry(serial)
    if telemetry is None:
        return None
    power = getattr(telemetry, "temp_control_power", None)
    if power is not None:
        return power

    measurements = getattr(telemetry, "measurements", None)
    if isinstance(measurements, dict):
        for key in ("temp_control_power", "peltier_power", "peltier_mode"):
            if key in measurements:
                return measurements[key]
    return None


def _peltier_mode(power):
    """Map signed Peltier power to a human-readable mode."""
    if power is None:
        return None
    if power < 0:
        return "Cooling"
    if power > 0:
        return "Warming"
    return "Idle"


def _button_state(value):
    """Map button telemetry values to on/off tokens."""
    if value is None:
        return None
    try:
        return "On" if float(value) >= 1.0 else "Off"
    except (TypeError, ValueError):
        return None


def _current_stage_group(coordinator, device_id):
    """Return the top-level overview group for a device serial."""
    if not coordinator.last_update_success or coordinator.data is None:
        return "unknown"
    for group_name, devices in coordinator.data.__dict__.items():
        for dev in devices:
            if _device_serial(dev) == device_id:
                return group_name
    return "unknown"


def _process_phase_display(device):
    """Return a readable ProcessPhase label from telemetry."""
    if not device:
        return None

    raw_phase = device.get("process_phase")
    if raw_phase is None:
        return None

    try:
        phase_name = ProcessPhase(int(raw_phase)).name.lower()
    except (TypeError, ValueError):
        return None

    if phase_name == "phase_none":
        return "None"

    for prefix in ("brew_", "ferm_", "serv_", "clean_mb_", "acid_clean_mb_"):
        if phase_name.startswith(prefix):
            phase_name = phase_name[len(prefix):]
            break

    return phase_name.replace("_", " ").title()


_CURRENT_STAGE_LABELS = {
    "brew_clean_idle": "Clean and ready to brew",
    "fermenting": "Fermenting",
    "serving": "Serving",
    "brew_acid_clean_idle": "Ready to clean",
    "unknown": "Unknown",
}


_CLOUD_CONNECTION_LABELS = {
    "online": "Online",
    "offline": "Offline",
}


_UPDATE_STATUS_LABELS = {
    "updating": "Updating",
    "not_updating": "Not updating",
}


_USER_ACTION_REQUIRED_LABELS = {
    "action_required": "Action required",
    "no_action_required": "No action required",
    "unknown": "Unknown",
}


_NEEDS_CLEANING_LABELS = {
    "needs_cleaning": "Needs cleaning",
    "clean": "Clean",
}


_PELTIER_MODE_LABELS = {
    "cooling": "Cooling",
    "warming": "Warming",
    "idle": "Idle",
}


def _user_action_required_state(device):
    """Map user_action/user_action_name to required-state token."""
    if not device:
        return None

    action = device.get("user_action")
    try:
        action_value = int(float(action)) if action is not None else None
    except (TypeError, ValueError):
        action_value = None

    if action_value is not None:
        return "action_required" if action_value > 0 else "no_action_required"

    action_name = str(device.get("user_action_name") or "").strip().lower()
    if action_name in {"action_undefined", "action_undified", "undefined"}:
        return "no_action_required"
    if action_name in {"", "unknown", "none", "null"}:
        return None
    return "action_required"


def _is_custom_fermentation_mode(device, *, current_stage=None):
    """Return True when payload indicates or strongly implies custom fermentation mode."""
    if not device:
        return False

    beer_name = str(device.get("beer_name") or "").strip().lower()
    beer_style = str(device.get("beer_style") or "").strip().lower()
    if beer_name == "custom fermentation" or beer_style == "custom fermentation":
        return True

    unknown_tokens = {"", "unknown", "n/a", "none", "null"}
    process_phase = str(_process_phase_display(device) or "").strip().lower()
    stage = str(current_stage or "").strip().lower()
    return (
        stage == "fermenting"
        and process_phase == "primary"
        and beer_name in unknown_tokens
        and beer_style in unknown_tokens
    )


def _required_action_description(device):
    """Describe the reconciled action without inventing a prompt for unknown codes."""
    state = _user_action_required_state(device)
    if state is None:
        return None
    if state == "no_action_required":
        return "No action required"
    value = device.get("user_action")
    try:
        action = UserAction(int(value))
    except (TypeError, ValueError):
        if value is not None:
            return f"Unknown action ({value})"
        name = device.get("user_action_name")
        return str(name).removeprefix("ACTION_").replace("_", " ").capitalize()
    return action.name.removeprefix("ACTION_").replace("_", " ").capitalize()


class _ActionDiagnosticsMixin:
    """Expose source disagreements without changing the reconciled state."""

    @property
    def extra_state_attributes(self):
        coordinator = self.coordinator
        rest = coordinator.get_rest_device(self.device_id)
        if not coordinator.last_update_success or not rest or rest.get("online") is not True:
            rest = {}
        mqtt = coordinator.get_telemetry(self.device_id)
        device = self._get_latest_device() or {}
        rest_action = rest.get("user_action")
        mqtt_action = getattr(mqtt, "user_action", None)
        rest_session, session_present = _session_id_from_device_dict(rest)
        mqtt_session = getattr(mqtt, "session_id", None)
        session_matches = (
            not session_present or mqtt_session is None
            or _coerce_session_id(mqtt_session) == rest_session
        )
        deadline = _next_action_state(device.get("process_estimate_remaining"))
        action_state = _user_action_required_state(device)
        waiting_since = None
        if action_state == "action_required" and device.get("user_action") == rest_action:
            # This is the reported state-entry time, not an inferred deadline.
            waiting_since = _coerce_timestamp(rest.get("last_process_state_change"))
        return {
            "required_action": _required_action_description(device),
            "action_status": (
                "Waiting for user action" if action_state == "action_required"
                else "No action required" if action_state == "no_action_required"
                else None
            ),
            "waiting_since": waiting_since,
            "rest_user_action": rest_action,
            "mqtt_user_action": mqtt_action,
            "rest_next_action_at": _coerce_timestamp(rest.get("process_estimate_remaining")),
            "mqtt_next_action_at": _coerce_timestamp(getattr(mqtt, "next_action_at", None)),
            "mqtt_seconds_until_next_action": getattr(mqtt, "seconds_until_next_action", None),
            "mqtt_session_matches": session_matches if mqtt is not None else None,
            "action_conflict": (
                rest_action != mqtt_action
                if session_matches and rest_action is not None and mqtt_action is not None
                else None
            ),
            "deadline_status": (
                "provided" if deadline is not None
                else "deadline_not_provided"
                if action_state == "action_required"
                else "not_provided"
            ),
        }


class _RequiredActionMixin(_ActionDiagnosticsMixin):
    _attr_translation_key = "required_action"
    _attr_icon = "mdi:clipboard-alert-outline"

    @property
    def name(self):
        return "Required Action"

    @property
    def native_value(self):
        return _required_action_description(self._get_latest_device())

    @property
    def unique_id(self):
        return f"{self.device_id}_required_action"


def _disable_legacy_esp_core_temp_entities(hass, config_entry):
    """Disable previously enabled ESP32 core temperature entities."""
    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if not entry.unique_id.endswith("_esp_core_temp"):
            continue
        if entry.disabled_by == RegistryEntryDisabler.INTEGRATION:
            continue
        registry.async_update_entity(
            entry.entity_id,
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )


def _migrate_legacy_fan_entities(hass, config_entry):
    """Migrate legacy numeric fan entities (e.g. ``..._4``) to stable IDs."""
    registry = er.async_get(hass)
    entries = list(er.async_entries_for_config_entry(registry, config_entry.entry_id))
    existing_unique_ids = {entry.unique_id for entry in entries}

    for entry in entries:
        unique_id = entry.unique_id or ""
        if not unique_id.endswith("_4"):
            continue

        base_unique_id = unique_id.rsplit("_", 1)[0]
        new_unique_id = f"{base_unique_id}_peltier_fan_power"
        if new_unique_id in existing_unique_ids:
            continue

        update_kwargs = {"new_unique_id": new_unique_id}
        if entry.entity_id.startswith("sensor.") and entry.entity_id.endswith("_4"):
            update_kwargs["new_entity_id"] = f"{entry.entity_id[:-2]}_fan_duty"

        registry.async_update_entity(entry.entity_id, **update_kwargs)
        existing_unique_ids.add(new_unique_id)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MiniBrew sensors from a config entry."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    client = store["client"]  # Get the BreweryClient instance
    added_devices = set()

    # Create a DataUpdateCoordinator
    coordinator = MiniBrewDataUpdateCoordinator(hass, client, config_entry)
    await coordinator.async_config_entry_first_refresh()
    _disable_legacy_esp_core_temp_entities(hass, config_entry)
    _migrate_legacy_fan_entities(hass, config_entry)

    # Expose the coordinator for unload (so the MQTT stream can be stopped).
    store["coordinator"] = coordinator

    # Start the optional real-time MQTT stream and subscribe to known devices.
    if coordinator.realtime_enabled:
        manager = MiniBrewRealtimeManager(hass, coordinator, client)
        coordinator.realtime = manager
        await manager.async_start(_mqtt_serials(coordinator.data))

    _LOGGER.debug("MiniBrew sensor setup complete: devices=%s realtime_enabled=%s", len(_collect_serials(coordinator)), coordinator.realtime_enabled)
    
    # Function to add new sensors dynamically
    def add_new_sensors():
        # Track devices currently in the API response
        current_devices = set()
        new_sensors = []
        
        for state, devices in coordinator.data.__dict__.items():  # Access states dynamically
            for device_data in devices:
                serial_number = _device_serial(device_data)
                if not serial_number:
                    continue
                
                current_devices.add(serial_number)
                
                # Check if the device has already been added
                if serial_number in added_devices:
                    continue

                # Convert the raw dictionary to a Device object
                device_dict = _device_to_dict(device_data)
                device = device_data if isinstance(device_data, Device) else Device(**device_dict)

                # Add sensors for MiniBrew devices
                if device.device_type == 0:  # Craft device
                    new_sensors.append(CraftSensorCurrentTemperatureSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorTargetTemperatureSensor(coordinator, device, state))
                    new_sensors.append(CraftBeerStyleSensor(coordinator, device, state))
                    new_sensors.append(CraftBeerNameSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorOnlineStatusSensor(coordinator, device, state))
                    new_sensors.append(CraftLastTimeOnlineSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorIsUpdatingSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorCurrentStageSensor(coordinator, device, state))
                    new_sensors.append(CraftProcessPhaseSensor(coordinator, device, state))
                    new_sensors.append(CraftUserActionRequiredSensor(coordinator, device, state))
                    new_sensors.append(CraftRequiredActionSensor(coordinator, device, state))
                    new_sensors.append(CraftNextActionDateTimeSensor(coordinator, device, state))
                    new_sensors.append(CraftSessionIdSensor(coordinator, device, state))
                    new_sensors.append(CraftSessionStartedSensor(coordinator, device, state))
                    if coordinator.realtime_enabled:
                        new_sensors.append(CraftEspCoreTempSensor(coordinator, device, state))
                        new_sensors.append(CraftButtonSensor(coordinator, device, state))
                # Add sensors for Keg devices
                elif device.device_type == 1:  # Keg device
                    new_sensors.append(KegCurrentTemperatureSensor(coordinator, device, state))
                    new_sensors.append(KegTargetTemperatureSensor(coordinator, device, state))
                    new_sensors.append(KegBeerStyleSensor(coordinator, device, state))
                    new_sensors.append(KegBeerNameSensor(coordinator, device, state))
                    new_sensors.append(KegCurrentStageSensor(coordinator, device, state))
                    new_sensors.append(KegProcessPhaseSensor(coordinator, device, state))
                    new_sensors.append(KegOnlineStatusSensor(coordinator, device, state))
                    new_sensors.append(KegLastTimeOnlineSensor(coordinator, device, state))
                    new_sensors.append(KegIsUpdatingSensor(coordinator, device, state))
                    new_sensors.append(KegActionRequiredSensor(coordinator, device, state))
                    new_sensors.append(KegRequiredActionSensor(coordinator, device, state))
                    new_sensors.append(KegNextActionDateTimeSensor(coordinator, device, state))
                    new_sensors.append(KegSessionIdSensor(coordinator, device, state))
                    new_sensors.append(KegSessionStartedSensor(coordinator, device, state))
                    if coordinator.realtime_enabled:
                        new_sensors.append(KegTempControlPowerSensor(coordinator, device, state))
                        new_sensors.append(KegPeltierModeSensor(coordinator, device, state))
                        new_sensors.append(KegPeltierFanPowerSensor(coordinator, device, state))
                        new_sensors.append(KegEspCoreTempSensor(coordinator, device, state))
                        new_sensors.append(KegButtonSensor(coordinator, device, state))
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

    config_entry.async_on_unload(
        coordinator.async_add_listener(handle_coordinator_update)
    )

class MiniBrewDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching MiniBrew data from the API."""

    def __init__(self, hass, client, config_entry):
        """Initialize the coordinator."""
        self.client = client
        self.config_entry = config_entry

        options = config_entry.options
        self.realtime_enabled = True
        realtime_poll_interval = options.get(
            CONF_REALTIME_POLL_INTERVAL, DEFAULT_REALTIME_POLL_INTERVAL
        )

        # MQTT is the primary source for fast-changing fields. REST polling is
        # retained at a slower cadence for discovery and fallback fields.
        poll_interval = realtime_poll_interval

        # Set by async_setup_entry once the MQTT stream is started.
        self.realtime = None
        self._session_created_by_id = {}
        self._session_created_by_serial = {}
        self._active_session_by_serial = {}
        self._session_lookup_inflight = set()
        self._session_model_mismatch_logged = False
        self._online_since = {}

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
        except Exception as err:
            if self.realtime is not None:
                await self.realtime.async_set_online_devices(set())
            if _is_auth_error(err):
                raise ConfigEntryAuthFailed("MiniBrew credentials are invalid") from err
            _LOGGER.error("Error fetching brewery overview: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

        try:
            devices = await self.hass.async_add_executor_job(self.client.get_devices)
            _merge_device_status_from_devices(data, devices)
        except Exception as err:  # noqa: BLE001 - supplemental endpoint; don't fail the whole update
            _LOGGER.warning("MiniBrew: could not fetch device list (device status may be stale): %s", err)

        try:
            await self._async_refresh_session_metadata(data)
        except Exception as err:
            if _is_auth_error(err):
                raise ConfigEntryAuthFailed("MiniBrew credentials are invalid") from err
            _LOGGER.error("Error fetching data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

        _LOGGER.debug("Fetched MiniBrew overview: groups=%s active_sessions=%s", {k: len(v) for k, v in data.__dict__.items()}, sum(1 for session_id in self._active_session_by_serial.values() if session_id is not None))

        # REST connectivity is authoritative for opening the MQTT stream.
        if self.realtime is not None:
            await self.realtime.async_set_online_devices(_mqtt_serials(data))

        return data

    def get_telemetry(self, serial):
        """Return the latest MQTT telemetry for a serial, or ``None``."""
        if not self.realtime_enabled or self.realtime is None:
            return None
        return self.realtime.get_telemetry(serial)

    def get_rest_device(self, serial):
        """Find a device even after its overview stage changes."""
        if self.data is not None:
            for devices in self.data.__dict__.values():
                for device in devices:
                    if _device_serial(device) == serial:
                        return _device_to_dict(device)
        return None

    def is_device_online(self, serial):
        """Require a positive cloud status or fresh device-specific telemetry."""
        return _cloud_connection_state(self, serial) == "Online"

    def get_online_since(self, serial):
        """Return the start of this continuously observed online period."""
        if not self.is_device_online(serial):
            self._online_since.pop(serial, None)
            return None
        return self._online_since.setdefault(serial, datetime.now(timezone.utc))

    def async_update_listeners(self):
        """Track connectivity transitions even if no timestamp entity is read."""
        for serial in _collect_serials(self) | set(self._online_since):
            self.get_online_since(serial)
        super().async_update_listeners()

    def get_realtime_last_update(self, serial):
        """Return timestamp of latest meaningful realtime telemetry update."""
        if self.realtime is None:
            return None
        return self.realtime.get_last_update(serial)

    def get_session_id(self, serial):
        """Return active session ID for a serial, or ``None``."""
        return self._active_session_by_serial.get(serial)

    def get_session_created(self, serial):
        """Return active session created timestamp for a serial, or ``None``."""
        return self._session_created_by_serial.get(serial)

    @property
    def realtime_connected(self):
        """Return whether the real-time MQTT stream is connected."""
        return self.realtime is not None and self.realtime.connected

    def get_merged_device(self, serial, state):
        """Return the REST device dict for a serial, overlaid with live telemetry.

        REST-only metadata is not used after a failed refresh. Session-specific
        telemetry must match an explicitly reported REST session.
        """
        device_dict = self.get_rest_device(serial)
        telemetry = self.get_telemetry(serial)
        if device_dict is None and telemetry is None:
            return None
        merged = dict(device_dict) if device_dict and self.last_update_success else {}
        if merged.get("online") is not True:
            for key in (
                "current_temp", "target_temp", "user_action", "user_action_name",
                "process_phase", "process_estimate_remaining",
                "process_estimate_remaining_seconds", "updating", "needs_acid_cleaning",
            ):
                merged.pop(key, None)
        rest_session, session_present = _session_id_from_device_dict(merged)
        if (
            not session_present
            and serial in self._active_session_by_serial
            and self.last_update_success
        ):
            rest_session = self._active_session_by_serial[serial]
            session_present = True
        if telemetry is not None:
            telemetry_session = getattr(telemetry, "session_id", None)
            include_session = (
                not session_present
                or telemetry_session is None
                or _coerce_session_id(telemetry_session) == rest_session
            )
            overlay_mqtt(merged, telemetry, include_session=include_session)
        if session_present:
            merged["active_session"] = rest_session
        active_session, _ = _session_id_from_device_dict(merged)
        if active_session is None:
            merged.pop("active_session", None)
            merged.pop("session_created", None)
        else:
            merged["active_session"] = active_session
            session_created = self._session_created_by_id.get(active_session)
            if session_created is not None:
                merged["session_created"] = session_created
            elif self.last_update_success:
                self.hass.async_create_task(
                    self._async_ensure_session_created(serial, active_session)
                )
        return merged

    async def _async_ensure_session_created(self, serial, session_id):
        """Populate session start cache for an active session when missing."""
        lookup_key = (serial, session_id)
        if lookup_key in self._session_lookup_inflight:
            return
        self._session_lookup_inflight.add(lookup_key)

        try:
            if self._active_session_by_serial.get(serial) != session_id:
                return
            cached_created = self._session_created_by_id.get(session_id)
            if cached_created is not None:
                self._session_created_by_serial[serial] = cached_created
                return

            try:
                created = await self._async_fetch_session_started(session_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("MiniBrew lazy session lookup failed for %s: %s", session_id, err)
                return
            if created is None:
                _LOGGER.debug("MiniBrew lazy session lookup returned no start timestamp for %s", session_id)
                return

            if self._active_session_by_serial.get(serial) != session_id:
                return
            self._session_created_by_id[session_id] = created
            self._session_created_by_serial[serial] = created
            _LOGGER.debug("MiniBrew cached session start for %s (%s): %s", serial, session_id, created)
            self.async_update_listeners()
        finally:
            self._session_lookup_inflight.discard(lookup_key)

    def _sync_fetch_session_started(self, session_id):
        """Fetch session-start timestamp with compatibility fallback."""
        try:
            session = self.client.get_session_info(session_id)
            return _session_started_from_session(session)
        except TypeError as err:
            message = str(err)
            if "unexpected keyword argument" not in message:
                raise
            if not self._session_model_mismatch_logged:
                _LOGGER.debug(
                    "MiniBrew session model mismatch for %s (%s); falling back to raw REST payload (further repeats suppressed)",
                    session_id,
                    err,
                )
                self._session_model_mismatch_logged = True

        rest_client = getattr(self.client, "client", None)
        get_method = getattr(rest_client, "get", None)
        if get_method is None:
            raise RuntimeError("MiniBrew REST fallback unavailable: missing client.get")

        response = get_method(f"v1/sessions/{session_id}")
        payload = response.json() if hasattr(response, "json") else None
        return _session_started_from_session(payload)

    async def _async_fetch_session_started(self, session_id):
        """Async wrapper for session-start fetch with compatibility fallback."""
        return await self.hass.async_add_executor_job(self._sync_fetch_session_started, session_id)

    async def _async_refresh_session_metadata(self, overview):
        """Refresh active-session IDs and start timestamps for known serials."""
        session_by_serial = {}
        for grouped_devices in overview.__dict__.values():
            for device in grouped_devices:
                device_dict = _device_to_dict(device)
                serial = device_dict.get("serial_number")
                if not serial:
                    continue
                session_id, session_key_present = _session_id_from_device_dict(device_dict)
                if session_key_present:
                    # REST is authoritative when any known session key is present.
                    pass
                else:
                    session_id = None
                    if self.realtime_enabled:
                        telemetry = self.get_telemetry(serial)
                        if telemetry is not None:
                            telemetry_session = getattr(telemetry, "session_id", None)
                            if telemetry_session is not None:
                                session_id = _coerce_session_id(telemetry_session)
                session_by_serial[serial] = session_id

        self._active_session_by_serial = session_by_serial
        active_session_ids = {
            session_id for session_id in session_by_serial.values()
            if session_id is not None
        }
        for session_id in list(self._session_created_by_id):
            if session_id not in active_session_ids:
                self._session_created_by_id.pop(session_id, None)

        for serial, session_id in session_by_serial.items():
            if session_id is None:
                self._session_created_by_serial.pop(serial, None)
                continue

            cached_created = self._session_created_by_id.get(session_id)
            if cached_created is not None:
                self._session_created_by_serial[serial] = cached_created
                continue

            try:
                created = await self._async_fetch_session_started(session_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("MiniBrew session lookup failed for %s: %s", session_id, err)
                self._session_created_by_serial.pop(serial, None)
                continue
            if created is not None:
                self._session_created_by_id[session_id] = created
                self._session_created_by_serial[serial] = created
            else:
                # Do not cache missing timestamps permanently; retry on next refresh.
                self._session_created_by_serial.pop(serial, None)

        active_serials = {
            serial for serial, session_id in session_by_serial.items() if session_id is not None
        }
        for serial in list(self._session_created_by_serial):
            if serial not in active_serials:
                self._session_created_by_serial.pop(serial, None)

class CraftSensor(SensorEntity):
    """Base class for MiniBrew sensors."""

    _data_source = "device"

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
            "sw_version": _firmware_version(device.software_version),
            "serial_number": device.serial_number,
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device.serial_number}_{self.name}"

    @property
    def available(self):
        """Return whether this sensor's required source is reachable."""
        return _sensor_available(self)

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
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_target_temperature"
class CraftBeerStyleSensor(CraftSensor):
    """Sensor for the beer style of the Craft device."""

    _attr_translation_key = "beer_style"
    _data_source = "rest"

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Beer Style"

    
    @property
    def native_value(self):
        """Return the beer style."""
        device = self._get_latest_device()
        if not device:
            return None
        current_stage = _current_stage_group(self.coordinator, self.device_id)
        if _is_custom_fermentation_mode(device, current_stage=current_stage):
            return "N/A"
        value = device.get("beer_style")
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    
    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_beer_style"


class CraftBeerNameSensor(CraftSensor):
    """Sensor for the beer name of the Craft device."""

    _attr_translation_key = "beer_name"
    _data_source = "rest"

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Beer Name"

    
    @property
    def native_value(self):
        """Return the beer name."""
        device = self._get_latest_device()
        if not device:
            return None
        current_stage = _current_stage_group(self.coordinator, self.device_id)
        if _is_custom_fermentation_mode(device, current_stage=current_stage):
            return "Custom Fermentation"
        value = device.get("beer_name")
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer-outline"

    
    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_beer_name"




class CraftSensorOnlineStatusSensor(CraftSensor):
    """Sensor for the online status of the Craft device."""

    _attr_translation_key = "cloud_connection"
    _data_source = "connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CLOUD_CONNECTION_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    
    
    def native_value(self):
        """Return the online status."""
        return _cloud_connection_state(self.coordinator, self.device_id)

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


class CraftLastTimeOnlineSensor(CraftSensor):
    """Sensor for the most recent online timestamp of the Craft device."""

    _attr_translation_key = "last_time_online"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Online Since"

    @property
    def native_value(self):
        """Return when the device was first observed online this connection."""
        return self.coordinator.get_online_since(self.device_id)

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:clock-check-outline"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_last_time_online"


class CraftSensorIsUpdatingSensor(CraftSensor):
    """Sensor for the update status of the Craft device."""

    _attr_translation_key = "update_status"
    _data_source = "rest_device"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _UPDATE_STATUS_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    
    
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        if not device or device.get("updating") is None:
            return None
        return "Updating" if device.get("updating") else "Not updating"

    
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

class CraftRequiredActionSensor(_RequiredActionMixin, CraftSensor):
    """Readable prompt for the Craft device's reconciled user action."""


class CraftUserActionRequiredSensor(_ActionDiagnosticsMixin, CraftSensor):
    """Sensor for user action required status of the Craft device."""

    _attr_translation_key = "user_action_required"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _USER_ACTION_REQUIRED_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    
    
    def native_value(self):
        """Return the user action required status."""
        return _display_option(
            _user_action_required_state(self._get_latest_device()),
            _USER_ACTION_REQUIRED_LABELS,
        )


    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        if self.native_value is None:
            return "mdi:help-circle-outline"
        if self.native_value == "Action required":
            return "mdi:alert"
        return "mdi:check-circle"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_user_action_required"


class CraftNextActionDateTimeSensor(_ActionDiagnosticsMixin, CraftSensor):
    """Sensor for the actual timestamp of the next required action."""

    _attr_translation_key = "next_action_time_remaining"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Next Action"

    @property
    def native_value(self):
        """Return the current deadline without retaining a previous session."""
        device = self._get_latest_device()
        if not device:
            return None

        current_stage = _current_stage_group(self.coordinator, self.device_id)
        user_action_required = _user_action_required_state(device)
        if current_stage in {"brew_clean_idle", "brew_acid_clean_idle"} and user_action_required != "action_required":
            return None

        return _next_action_state(device.get("process_estimate_remaining"))

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


class CraftSessionIdSensor(CraftSensor):
    """Sensor for the active brew session ID of the Craft device."""

    _attr_translation_key = "session_id"
    _data_source = "rest"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Session ID"

    @property
    def native_value(self):
        """Return active session ID from merged device/coordinator state."""
        device = self._get_latest_device()
        if not device:
            return None
        session_id, _ = _session_id_from_device_dict(device)
        return session_id

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:identifier"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_session_id"


class CraftSessionStartedSensor(CraftSensor):
    """Sensor for the active brew session start timestamp of the Craft device."""

    _attr_translation_key = "session_started"
    _data_source = "rest"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Brew Session Started"

    @property
    def native_value(self):
        """Return active session start timestamp from session.created."""
        device = self._get_latest_device()
        if not device:
            return None

        session_id, _ = _session_id_from_device_dict(device)
        if session_id is None:
            return None

        started = _coerce_timestamp(device.get("session_created"))
        if started is not None:
            return started

        self.coordinator.hass.async_create_task(
            self.coordinator._async_ensure_session_created(self.device_id, session_id)
        )
        return None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:calendar-start"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_session_started"


class CraftSensorCurrentStageSensor(CraftSensor):
    """Sensor for the current stage of the Craft device."""

    _attr_translation_key = "current_stage"
    _data_source = "rest"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CURRENT_STAGE_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    
    
    def native_value(self):
        """Return a human-readable phase name based on the device's group."""
        return _display_option(
            _current_stage_group(self.coordinator, self.device_id),
            _CURRENT_STAGE_LABELS,
        )

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_current_stage"

class CraftProcessPhaseSensor(CraftSensor):
    """Sensor for the current process phase of the Craft device."""

    _attr_translation_key = "process_phase"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _PROCESS_PHASE_OPTIONS

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Process Phase"

    @property
    def native_value(self):
        """Return current process phase label from telemetry."""
        return _process_phase_display(self._get_latest_device())

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:timeline-clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_process_phase"


class CraftSensorNeedsCleaningSensor(CraftSensor):
    """Sensor for the cleaning status of the Craft device."""

    _attr_translation_key = "needs_cleaning"
    _data_source = "rest_device"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _NEEDS_CLEANING_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    
    
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        if not device or device.get("needs_acid_cleaning") is None:
            return None
        return "Needs cleaning" if device["needs_acid_cleaning"] else "Clean"

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

    _data_source = "device"

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
            "sw_version": _firmware_version(device.software_version),
            "serial_number": device.serial_number,
        }

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self.device_id}_{self.name}"
    
    @property
    def available(self):
        """Return whether this sensor's required source is reachable."""
        return _sensor_available(self)

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
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_{self.name}"

class KegBeerStyleSensor(KegSensor):
    """Sensor for the beer style of the Keg device."""

    _attr_translation_key = "beer_style"
    _data_source = "rest"

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Beer Style"

    @property
    
    
    def native_value(self):
        """Return the beer style."""
        device = self._get_latest_device()
        if not device:
            return None
        current_stage = _current_stage_group(self.coordinator, self.device_id)
        if _is_custom_fermentation_mode(device, current_stage=current_stage):
            return "N/A"
        value = device.get("beer_style")
        if isinstance(value, str):
            value = value.strip()
        return value or None

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
    _data_source = "rest"

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Beer Name"

    @property
    
    
    def native_value(self):
        """Return the beer name."""
        device = self._get_latest_device()
        if not device:
            return None
        current_stage = _current_stage_group(self.coordinator, self.device_id)
        if _is_custom_fermentation_mode(device, current_stage=current_stage):
            return "Custom Fermentation"
        value = device.get("beer_name")
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer-outline"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_{self.name}"


class KegCurrentStageSensor(KegSensor):
    """Sensor for the current stage of the Keg device."""

    _attr_translation_key = "current_stage"
    _data_source = "rest"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CURRENT_STAGE_OPTIONS

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    def native_value(self):
        """Return the current high-level stage from the overview group."""
        return _display_option(
            _current_stage_group(self.coordinator, self.device_id),
            _CURRENT_STAGE_LABELS,
        )

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:beer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_current_stage"


class KegProcessPhaseSensor(KegSensor):
    """Sensor for the current process phase of the Keg device."""

    _attr_translation_key = "process_phase"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _PROCESS_PHASE_OPTIONS

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Process Phase"

    @property
    def native_value(self):
        """Return current process phase label from telemetry."""
        return _process_phase_display(self._get_latest_device())

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:timeline-clock"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_process_phase"


class KegOnlineStatusSensor(KegSensor):
    """Sensor for the online status of the Keg device."""

    _attr_translation_key = "cloud_connection"
    _data_source = "connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CLOUD_CONNECTION_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    
    
    def native_value(self):
        """Return the online status."""
        return _cloud_connection_state(self.coordinator, self.device_id)

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


class KegLastTimeOnlineSensor(KegSensor):
    """Sensor for the most recent online timestamp of the Keg device."""

    _attr_translation_key = "last_time_online"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Online Since"

    @property
    def native_value(self):
        """Return when the device was first observed online this connection."""
        return self.coordinator.get_online_since(self.device_id)

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:clock-check-outline"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_last_time_online"


class KegIsUpdatingSensor(KegSensor):
    """Sensor for the update status of the Keg device."""

    _attr_translation_key = "update_status"
    _data_source = "rest_device"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _UPDATE_STATUS_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Update Status"

    @property
    
    
    def native_value(self):
        """Return the update status."""
        device = self._get_latest_device()
        if not device or device.get("updating") is None:
            return None
        return "Updating" if device.get("updating") else "Not updating"

    
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
    _data_source = "rest_device"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _NEEDS_CLEANING_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Needs Cleaning"

    @property
    
    
    def native_value(self):
        """Return the cleaning status."""
        device = self._get_latest_device()
        if not device or device.get("needs_acid_cleaning") is None:
            return None
        return "Needs cleaning" if device["needs_acid_cleaning"] else "Clean"

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

class KegRequiredActionSensor(_RequiredActionMixin, KegSensor):
    """Readable prompt for the Keg device's reconciled user action."""


class KegActionRequiredSensor(_ActionDiagnosticsMixin, KegSensor):
    """Sensor for user action required status of the Keg device."""

    _attr_translation_key = "user_action_required"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _USER_ACTION_REQUIRED_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    
    
    def native_value(self):
        """Return the user action required status."""
        return _display_option(
            _user_action_required_state(self._get_latest_device()),
            _USER_ACTION_REQUIRED_LABELS,
        )


    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        if self.native_value is None:
            return "mdi:help-circle-outline"
        if self.native_value == "Action required":
            return "mdi:alert"
        return "mdi:check-circle"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_{self.name}"


class KegNextActionDateTimeSensor(_ActionDiagnosticsMixin, KegSensor):
    """Sensor for the actual timestamp of the next required action."""

    _attr_translation_key = "next_action_time_remaining"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Next Action"

    @property
    def native_value(self):
        """Return the current deadline without retaining a previous session."""
        device = self._get_latest_device()
        if not device:
            return None

        current_stage = _current_stage_group(self.coordinator, self.device_id)
        if current_stage in {"brew_clean_idle", "brew_acid_clean_idle"} and _user_action_required_state(device) != "action_required":
            return None
        return _next_action_state(device.get("process_estimate_remaining"))

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



class KegSessionIdSensor(KegSensor):
    """Sensor for the active brew session ID of the Keg device."""

    _attr_translation_key = "session_id"
    _data_source = "rest"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Session ID"

    @property
    def native_value(self):
        """Return active session ID from merged device/coordinator state."""
        device = self._get_latest_device()
        if not device:
            return None
        session_id, _ = _session_id_from_device_dict(device)
        return session_id

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:identifier"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_session_id"


class KegSessionStartedSensor(KegSensor):
    """Sensor for the active brew session start timestamp of the Keg device."""

    _attr_translation_key = "session_started"
    _data_source = "rest"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Brew Session Started"

    @property
    def native_value(self):
        """Return active session start timestamp from session.created."""
        device = self._get_latest_device()
        if not device:
            return None

        session_id, _ = _session_id_from_device_dict(device)
        if session_id is None:
            return None

        started = _coerce_timestamp(device.get("session_created"))
        if started is not None:
            return started

        self.coordinator.hass.async_create_task(
            self.coordinator._async_ensure_session_created(self.device_id, session_id)
        )
        return None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:calendar-start"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_session_started"


class CraftTempControlPowerSensor(CraftSensor):
    """Sensor for the temperature-control (Peltier) power of the Craft device (MQTT only)."""

    _attr_translation_key = "temp_control_power"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Peltier Power"

    
    
    
    @property
    def native_value(self):
        """Return the absolute Peltier power percentage."""
        power = _telemetry_temp_control_power(self.coordinator, self.device_id)
        return abs(power) if power is not None else None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:speedometer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_temp_control_power"


class KegTempControlPowerSensor(KegSensor):
    """Sensor for the temperature-control (Peltier) power of the Keg device (MQTT only)."""

    _attr_translation_key = "temp_control_power"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Peltier Power"

    
    
    
    @property
    def native_value(self):
        """Return the absolute Peltier power percentage."""
        power = _telemetry_temp_control_power(self.coordinator, self.device_id)
        return abs(power) if power is not None else None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:speedometer"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_temp_control_power"


class CraftPeltierFanPowerSensor(CraftSensor):
    """Sensor for the Peltier fan power of the Craft device (MQTT only)."""

    _attr_translation_key = "peltier_fan_power"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Fan Duty"

    
    
    
    @property
    def native_value(self):
        """Return Peltier fan power as a percentage."""
        value = _telemetry_sensor_value(
            self.coordinator, self.device_id, SensorType.PELTIER_FAN_POWER
        )
        return round(value) if value is not None else None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:fan"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_peltier_fan_power"


class KegPeltierFanPowerSensor(KegSensor):
    """Sensor for the Peltier fan power of the Keg device (MQTT only)."""

    _attr_translation_key = "peltier_fan_power"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Fan Duty"

    
    
    
    @property
    def native_value(self):
        """Return Peltier fan power as a percentage."""
        value = _telemetry_sensor_value(
            self.coordinator, self.device_id, SensorType.PELTIER_FAN_POWER
        )
        return round(value) if value is not None else None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:fan"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_peltier_fan_power"


class CraftEspCoreTempSensor(CraftSensor):
    """Sensor for ESP core temperature on the Craft device (MQTT only)."""

    _attr_translation_key = "esp_core_temp"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "ESP32 Core Temperature"

    
    
    
    @property
    def native_value(self):
        """Return ESP core temperature in Celsius."""
        return _telemetry_sensor_value(
            self.coordinator, self.device_id, SensorType.ESP_CORE_TEMP
        )

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:chip"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_esp_core_temp"


class KegEspCoreTempSensor(KegSensor):
    """Sensor for ESP core temperature on the Keg device (MQTT only)."""

    _attr_translation_key = "esp_core_temp"
    _data_source = "mqtt"
    _attr_native_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    
    
    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "ESP32 Core Temperature"

    
    
    
    @property
    def native_value(self):
        """Return ESP core temperature in Celsius."""
        return _telemetry_sensor_value(
            self.coordinator, self.device_id, SensorType.ESP_CORE_TEMP
        )

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:chip"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_esp_core_temp"

class CraftButtonSensor(CraftSensor):
    """Sensor for the button telemetry on the Craft device (MQTT only)."""

    _attr_translation_key = "button"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _BUTTON_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Button"

    
    @property
    def native_value(self):
        """Return button telemetry value."""
        value = _telemetry_sensor_value(self.coordinator, self.device_id, SensorType.BUTTON)
        return _button_state(value)

    
    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:gesture-tap-button"

    
    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_button"


class KegButtonSensor(KegSensor):
    """Sensor for the button telemetry on the Keg device (MQTT only)."""

    _attr_translation_key = "button"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _BUTTON_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Button"

    
    @property
    def native_value(self):
        """Return button telemetry value."""
        value = _telemetry_sensor_value(self.coordinator, self.device_id, SensorType.BUTTON)
        return _button_state(value)

    
    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:gesture-tap-button"

    
    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_button"


class CraftPeltierModeSensor(CraftSensor):
    """Sensor for the current Peltier mode of the Craft device (MQTT only)."""

    _attr_translation_key = "peltier_mode"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _PELTIER_MODE_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Peltier Mode"

    @property
    def native_value(self):
        """Return Peltier mode (cooling, warming, idle)."""
        return _peltier_mode(_telemetry_temp_control_power(self.coordinator, self.device_id))

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermostat"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_peltier_mode"


class KegPeltierModeSensor(KegSensor):
    """Sensor for the current Peltier mode of the Keg device (MQTT only)."""

    _attr_translation_key = "peltier_mode"
    _data_source = "mqtt"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _PELTIER_MODE_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Peltier Mode"

    @property
    def native_value(self):
        """Return Peltier mode (cooling, warming, idle)."""
        return _peltier_mode(_telemetry_temp_control_power(self.coordinator, self.device_id))

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermostat"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_peltier_mode"
