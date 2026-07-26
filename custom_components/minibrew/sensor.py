import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymbrewclient import Device, ProcessPhase, SensorType

from .const import (
    CONF_REALTIME_POLL_INTERVAL,
    DEFAULT_REALTIME_POLL_INTERVAL,
    DOMAIN,
)
from .realtime import MiniBrewRealtimeManager, overlay_mqtt

_LOGGER = logging.getLogger(__name__)

_USER_ACTION_REQUIRED_OPTIONS = [
    "action_required",
    "no_action_required",
    "unknown",
]

_CLOUD_CONNECTION_OPTIONS = [
    "online",
    "offline",
]

_UPDATE_STATUS_OPTIONS = [
    "updating",
    "not_updating",
]

_BUTTON_OPTIONS = [
    "on",
    "off",
]

_PELTIER_MODE_OPTIONS = [
    "cooling",
    "warming",
    "idle",
]

_CURRENT_STAGE_OPTIONS = [
    "brew_clean_idle",
    "fermenting",
    "serving",
    "brew_acid_clean_idle",
    "unknown",
]

_NEEDS_CLEANING_OPTIONS = [
    "needs_cleaning",
    "clean",
]


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
    """Return a stable next-action timestamp for Home Assistant."""
    ts = _coerce_timestamp(value)
    if ts is None:
        return None

    # Keep minute-level precision to avoid noisy state churn from
    # per-second server-side countdown recalculations.
    return ts.replace(second=0, microsecond=0)



def _effective_last_time_online(coordinator, serial, device):
    """Return a UI-friendly last-online timestamp."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    if coordinator.realtime_enabled and coordinator.get_telemetry(serial) is not None:
        return now

    online_flag = device.get("online") if isinstance(device, dict) else None
    if online_flag is True:
        return now

    return _coerce_timestamp(device.get("last_time_online")) if isinstance(device, dict) else None

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


def _merge_last_time_online_from_devices(overview, devices):
    """Fill missing overview last_time_online values from /v1/devices by serial."""
    if overview is None or not devices:
        return

    last_seen_by_serial = {}
    for device in devices:
        device_dict = _device_to_dict(device)
        serial = device_dict.get("serial_number")
        if not serial:
            continue
        last_seen = device_dict.get("last_time_online")
        if last_seen is not None:
            last_seen_by_serial[serial] = last_seen

    if not last_seen_by_serial:
        return

    for grouped_devices in overview.__dict__.values():
        for device in grouped_devices:
            device_dict = _device_to_dict(device)
            serial = device_dict.get("serial_number")
            if not serial:
                continue
            merged_last_seen = last_seen_by_serial.get(serial)
            if merged_last_seen is None:
                continue
            if isinstance(device, dict):
                device["last_time_online"] = merged_last_seen
            else:
                setattr(device, "last_time_online", merged_last_seen)


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


def _telemetry_sensor_value(coordinator, serial, sensor_type: SensorType, *, hide_zero: bool = False):
    """Return a typed measurement from the latest telemetry for a given serial."""
    telemetry = coordinator.get_telemetry(serial)
    if telemetry is None:
        return None

    value = telemetry.sensor(sensor_type)
    if hide_zero and value == 0.0:
        return None
    return value


def _telemetry_temp_control_power(coordinator, serial):
    """Return signed Peltier power from telemetry, or ``None`` when unavailable."""
    telemetry = coordinator.get_telemetry(serial)
    if telemetry is None:
        return None
    return telemetry.temp_control_power


def _peltier_mode(power):
    """Map signed Peltier power to a human-readable mode."""
    if power is None:
        return None
    if power < 0:
        return "cooling"
    if power > 0:
        return "warming"
    return "idle"


def _button_state(value):
    """Map button telemetry values to on/off tokens."""
    if value is None:
        return None
    try:
        return "on" if float(value) >= 1.0 else "off"
    except (TypeError, ValueError):
        return None


def _current_stage_group(coordinator, device_id):
    """Return the top-level overview group for a device serial."""
    for group_name, devices in coordinator.data.__dict__.items():
        for dev in devices:
            device_dict = _device_to_dict(dev)
            if device_dict.get("serial_number") == device_id:
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


def _user_action_required_state(device):
    """Map user_action/user_action_name to required-state token."""
    if not device:
        return "unknown"

    action = device.get("user_action")
    try:
        action_value = int(float(action)) if action is not None else None
    except (TypeError, ValueError):
        action_value = None

    if action_value is not None:
        return "action_required" if action_value > 0 else "no_action_required"

    action_name = str(device.get("user_action_name") or "").strip().lower()
    if action_name in {"action_undefined", "action_undified", "undefined", ""}:
        return "no_action_required"
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
        await manager.async_start()
        coordinator.realtime = manager
        manager.async_ensure_subscribed(_collect_serials(coordinator))

    _LOGGER.debug("MiniBrew sensor setup complete: devices=%s realtime_enabled=%s", len(_collect_serials(coordinator)), coordinator.realtime_enabled)
    
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
                    new_sensors.append(CraftBeerStyleSensor(coordinator, device, state))
                    new_sensors.append(CraftBeerNameSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorOnlineStatusSensor(coordinator, device, state))
                    new_sensors.append(CraftLastTimeOnlineSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorIsUpdatingSensor(coordinator, device, state))
                    new_sensors.append(CraftSensorCurrentStageSensor(coordinator, device, state))
                    new_sensors.append(CraftProcessPhaseSensor(coordinator, device, state))
                    new_sensors.append(CraftUserActionRequiredSensor(coordinator, device, state))
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

    coordinator.async_add_listener(handle_coordinator_update)

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
            devices = await self.hass.async_add_executor_job(self.client.get_devices)
            _merge_last_time_online_from_devices(data, devices)
            await self._async_refresh_session_metadata(data)
            _LOGGER.debug("Fetched MiniBrew overview: groups=%s active_sessions=%s", {k: len(v) for k, v in data.__dict__.items()}, sum(1 for session_id in self._active_session_by_serial.values() if session_id is not None))
        except Exception as err:
            if _is_auth_error(err):
                raise ConfigEntryAuthFailed("MiniBrew credentials are invalid") from err
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

        Returns ``None`` when the device is not present in the given state group.
        """
        devices = getattr(self.data, state, [])
        for dev in devices:
            device_dict = _device_to_dict(dev)
            if device_dict.get("serial_number") == serial:
                merged = dict(device_dict)
                if self.realtime_enabled:
                    _overlay_mqtt(merged, self.get_telemetry(serial))
                if serial in self._active_session_by_serial:
                    merged["active_session"] = self._active_session_by_serial[serial]
                active_session, _ = _session_id_from_device_dict(merged)
                if active_session is not None:
                    merged["active_session"] = active_session
                else:
                    merged.pop("active_session", None)
                    merged.pop("session_created", None)

                session_created = self._session_created_by_serial.get(serial)
                if session_created is None and active_session is not None:
                    session_created = self._session_created_by_id.get(active_session)
                    if session_created is None:
                        self.hass.async_create_task(
                            self._async_ensure_session_created(serial, active_session)
                        )
                if session_created is not None:
                    merged["session_created"] = session_created
                return merged
        return None

    async def _async_ensure_session_created(self, serial, session_id):
        """Populate session start cache for an active session when missing."""
        lookup_key = (serial, session_id)
        if lookup_key in self._session_lookup_inflight:
            return
        self._session_lookup_inflight.add(lookup_key)

        try:
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
class CraftBeerStyleSensor(CraftSensor):
    """Sensor for the beer style of the Craft device."""

    _attr_translation_key = "beer_style"

    
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
    def available(self):
        """Return True when beer style is known."""
        return self.native_value is not None


    
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
    def available(self):
        """Return True when beer name is known."""
        return self.native_value is not None


    
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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CLOUD_CONNECTION_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    
    
    def native_value(self):
        """Return the online status."""
        if self.coordinator.realtime_enabled and self.coordinator.get_telemetry(self.device_id) is not None:
            return "online"
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


class CraftLastTimeOnlineSensor(CraftSensor):
    """Sensor for the most recent online timestamp of the Craft device."""

    _attr_translation_key = "last_time_online"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Last Time Online"

    
    @property
    def native_value(self):
        """Return the last time this device was seen online."""
        device = self._get_latest_device()
        if not device:
            return None
        return _effective_last_time_online(self.coordinator, self.device_id, device)

    
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
        return "updating" if device.get("updating") else "not_updating"

    
    @property
    def available(self):
        """Return True when update status is known from the device payload."""
        return self.native_value is not None

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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _USER_ACTION_REQUIRED_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    
    
    def native_value(self):
        """Return the user action required status."""
        return _user_action_required_state(self._get_latest_device())


    @property
    def entity_category(self):
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        if self.native_value == "action_required":
            return "mdi:alert"
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
    def name(self):
        """Return the name of the sensor."""
        return "Next Action"

    
    @property
    def native_value(self):
        """Return next-action state; use \"now\" when imminently due."""
        device = self._get_latest_device()
        if not device:
            return None

        current_stage = _current_stage_group(self.coordinator, self.device_id)
        user_action_required = _user_action_required_state(device)
        if current_stage in {"brew_clean_idle", "brew_acid_clean_idle"} and user_action_required != "action_required":
            return None

        return _next_action_state(device.get("process_estimate_remaining"))

    @property
    def available(self):
        """Return True when a next action timestamp is available."""
        return self.native_value is not None


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
    def available(self):
        """Return True when an active session is present."""
        return self.native_value is not None

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
    def available(self):
        """Return True when active session start is known."""
        return self.native_value is not None

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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CURRENT_STAGE_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    
    
    def native_value(self):
        """Return a human-readable phase name based on the device's group."""
        return _current_stage_group(self.coordinator, self.device_id)

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
    def available(self):
        """Return True once process phase telemetry is available."""
        device = self._get_latest_device()
        return device is not None and device.get("process_phase") is not None

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
    def available(self):
        """Return True when beer style is known."""
        return self.native_value is not None


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
    def available(self):
        """Return True when beer name is known."""
        return self.native_value is not None


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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CURRENT_STAGE_OPTIONS

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Current Stage"

    @property
    def native_value(self):
        """Return the current high-level stage from the overview group."""
        return _current_stage_group(self.coordinator, self.device_id)

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
    def available(self):
        """Return True once process phase telemetry is available."""
        device = self._get_latest_device()
        return device is not None and device.get("process_phase") is not None

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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CLOUD_CONNECTION_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "Cloud Connection"

    @property
    
    
    def native_value(self):
        """Return the online status."""
        if self.coordinator.realtime_enabled and self.coordinator.get_telemetry(self.device_id) is not None:
            return "online"
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


class KegLastTimeOnlineSensor(KegSensor):
    """Sensor for the most recent online timestamp of the Keg device."""

    _attr_translation_key = "last_time_online"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    
    @property
    def name(self):
        """Return the name of the sensor."""
        return "Last Time Online"

    
    @property
    def native_value(self):
        """Return the last time this device was seen online."""
        device = self._get_latest_device()
        if not device:
            return None
        return _effective_last_time_online(self.coordinator, self.device_id, device)

    
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
        return "updating" if device.get("updating") else "not_updating"

    
    @property
    def available(self):
        """Return True when update status is known from the device payload."""
        return self.native_value is not None

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
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _USER_ACTION_REQUIRED_OPTIONS

    @property
    
    
    def name(self):
        """Return the name of the sensor."""
        return "User Action Required"

    @property
    
    
    def native_value(self):
        """Return the user action required status."""
        return _user_action_required_state(self._get_latest_device())


    @property
    def entity_category(self):
        """Return the entity category (diagnostic)."""
        return EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon for the sensor."""
        if self.native_value == "action_required":
            return "mdi:alert"
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
    def name(self):
        """Return the name of the sensor."""
        return "Next Action"

    
    @property
    def native_value(self):
        """Return next-action state; use \"now\" when imminently due."""
        device = self._get_latest_device()
        if not device:
            return None
        return _next_action_state(device.get("process_estimate_remaining"))

    @property
    def available(self):
        """Return True when a next action timestamp is available."""
        return self.native_value is not None


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
    def available(self):
        """Return True when an active session is present."""
        return self.native_value is not None

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
    def available(self):
        """Return True when active session start is known."""
        return self.native_value is not None

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
    def available(self):
        """Return True once real-time telemetry with a control-power reading has arrived."""
        return _telemetry_temp_control_power(self.coordinator, self.device_id) is not None

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
    def available(self):
        """Return True once real-time telemetry with a control-power reading has arrived."""
        return _telemetry_temp_control_power(self.coordinator, self.device_id) is not None

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
            self.coordinator, self.device_id, SensorType.PELTIER_FAN_POWER, hide_zero=True
        )
        return round(value) if value is not None else None

    @property
    def available(self):
        """Return True when a non-zero fan power telemetry value has arrived."""
        return self.native_value is not None

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
            self.coordinator, self.device_id, SensorType.PELTIER_FAN_POWER, hide_zero=True
        )
        return round(value) if value is not None else None

    @property
    def available(self):
        """Return True when a non-zero fan power telemetry value has arrived."""
        return self.native_value is not None

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
            self.coordinator, self.device_id, SensorType.ESP_CORE_TEMP, hide_zero=True
        )

    @property
    def available(self):
        """Return True once an ESP core temperature reading has arrived."""
        return self.native_value is not None

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
            self.coordinator, self.device_id, SensorType.ESP_CORE_TEMP, hide_zero=True
        )

    @property
    def available(self):
        """Return True once an ESP core temperature reading has arrived."""
        return self.native_value is not None

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
    def available(self):
        """Return True once a button telemetry value has arrived."""
        return self.native_value is not None

    
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
    def available(self):
        """Return True once a button telemetry value has arrived."""
        return self.native_value is not None

    
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
    def available(self):
        """Return True when Peltier telemetry has arrived."""
        return _telemetry_temp_control_power(self.coordinator, self.device_id) is not None

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
    def available(self):
        """Return True when Peltier telemetry has arrived."""
        return _telemetry_temp_control_power(self.coordinator, self.device_id) is not None

    @property
    def icon(self):
        """Return the icon for the sensor."""
        return "mdi:thermostat"

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return f"{self.device_id}_peltier_mode"
