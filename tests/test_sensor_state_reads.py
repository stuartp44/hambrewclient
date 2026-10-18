"""Exercise the actual sensor read methods without a Home Assistant runtime.

Only the relevant definitions are compiled from the source AST, so the CI test
environment needs no Home Assistant or cloud client dependencies.
"""

import ast
import asyncio
import dataclasses
import importlib.util
import json
import logging
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "minibrew"


@dataclasses.dataclass
class Device:
    serial_number: str
    online: bool | None = True
    beer_style: str | None = " IPA "
    beer_name: str | None = "Test beer"
    current_temp: float = 20.0
    target_temp: float = 18.0
    process_estimate_remaining: datetime | None = None
    active_session: int | None = None
    process_phase: int | None = None
    user_action: int | None = None
    process_state: int | None = None
    metadata: object = None


class ProcessPhase(IntEnum):
    PHASE_NONE = 0
    FERM_PRIMARY = 1


class ProcessType(IntEnum):
    PROC_IDLE = 0
    PROC_BREWING = 1
    PROC_CLEAN_MINIBREW = 2
    PROC_CLEAN_KEG = 3
    PROC_FERMENTATION = 4
    PROC_SERVING = 5
    PROC_ACID_CLEAN_MINIBREW = 6


class SensorType(IntEnum):
    PELTIER_FAN_POWER = 1
    ESP_CORE_TEMP = 2
    BUTTON = 3


class _Coordinator:
    def async_update_listeners(self):
        pass


class UserAction(IntEnum):
    ACTION_UNDEFINED = 0
    ACTION_ADD_INGREDIENT = 4
    ACTION_PLACE_TRUB_CONTAINER = 30


def _load_sensor_reads():
    realtime_spec = importlib.util.spec_from_file_location(
        "state_read_realtime", _ROOT / "realtime.py"
    )
    realtime = importlib.util.module_from_spec(realtime_spec)
    realtime_spec.loader.exec_module(realtime)
    names = {
        "_device_to_dict",
        "_device_serial",
        "_firmware_version",
        "_coerce_session_id",
        "_coerce_timestamp",
        "_next_action_state",
        "_display_option",
        "_user_action_required_state",
        "_required_action_description",
        "_session_id_from_device_dict",
        "_overlay_mqtt",
        "_current_stage_group",
        "_process_phase_display",
        "_is_custom_fermentation_mode",
        "_collect_serials",
        "_mqtt_serials",
        "_is_auth_error",
        "_merge_device_status_from_devices",
        "_sensor_available",
        "_cloud_connection_state",
        "_telemetry_sensor_value",
        "_telemetry_temp_control_power",
        "_peltier_mode",
        "_button_state",
        "MiniBrewDataUpdateCoordinator",
        "CraftSensor",
        "CraftBeerStyleSensor",
        "CraftBeerNameSensor",
        "CraftUserActionRequiredSensor",
        "CraftNextActionDateTimeSensor",
        "KegSensor",
        "KegBeerStyleSensor",
        "KegBeerNameSensor",
        "KegActionRequiredSensor",
        "KegNextActionDateTimeSensor",
    }
    path = _ROOT / "sensor.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    names.update(
        node.name for node in tree.body
        if isinstance(node, ast.ClassDef)
    )
    tree.body = [
        node for node in tree.body
        if getattr(node, "name", None) in names
        or (
            isinstance(node, ast.Assign)
            and isinstance(node.value, (ast.List, ast.Dict))
        )
    ]
    namespace = {
        "Device": Device,
        "ProcessPhase": ProcessPhase,
        "ProcessType": ProcessType,
        "SensorType": SensorType,
        "UserAction": UserAction,
        "SensorDeviceClass": SimpleNamespace(ENUM="enum", TIMESTAMP="timestamp"),
        "SensorStateClass": SimpleNamespace(MEASUREMENT="measurement"),
        "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
        "_PROCESS_PHASE_OPTIONS": [],
        "datetime": datetime,
        "timezone": timezone,
        "fields": dataclasses.fields,
        "is_dataclass": dataclasses.is_dataclass,
        "SensorEntity": object,
        "DataUpdateCoordinator": _Coordinator,
        "overlay_mqtt": realtime.overlay_mqtt,
        "MiniBrewRealtimeManager": realtime.MiniBrewRealtimeManager,
        "DOMAIN": "minibrew",
        "_LOGGER": logging.getLogger(__name__),
        "UpdateFailed": RuntimeError,
        "ConfigEntryAuthFailed": PermissionError,
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


sensor = _load_sensor_reads()


@pytest.mark.parametrize("class_name", ["CraftSensor", "KegSensor"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.2.3, idf-v4.2-50-g11005797d", "3.2.3"),
        ("3.2.3,idf-v4.2-50-g11005797d", "3.2.3"),
        ("3.2.3", "3.2.3"),
        ("3.2.3, other-build", "3.2.3, other-build"),
        (None, None),
        ("", ""),
    ],
)
def test_device_firmware_hides_only_idf_suffix(class_name, raw, expected):
    device = SimpleNamespace(
        serial_number="serial-1", title="MiniBrew", software_version=raw
    )
    entity = getattr(sensor, class_name)(None, device, "serving")
    assert entity._attr_device_info["sw_version"] == expected
    assert device.software_version == raw


def _coordinator(groups, *, telemetry=None):
    coordinator = sensor.MiniBrewDataUpdateCoordinator.__new__(
        sensor.MiniBrewDataUpdateCoordinator
    )
    coordinator.data = SimpleNamespace(**groups)
    coordinator.realtime_enabled = True
    coordinator.realtime = SimpleNamespace(
        get_telemetry=lambda serial: (telemetry or {}).get(serial)
    )
    coordinator._active_session_by_serial = {}
    coordinator._session_created_by_serial = {}
    coordinator._session_created_by_id = {}
    coordinator._online_since = {}
    coordinator.last_update_success = True
    coordinator.hass = SimpleNamespace(async_create_task=Mock())
    return coordinator


def _entity(class_name, coordinator, serial, state):
    entity = getattr(sensor, class_name).__new__(getattr(sensor, class_name))
    entity.coordinator = coordinator
    entity.device_id = serial
    entity.device_type = state
    return entity


class _NoDeepCopy:
    def __deepcopy__(self, memo):
        raise AssertionError(
            "Entity state reads must not recursively copy device fields"
        )


def test_beer_style_state_read_does_not_deepcopy():
    device = Device("serial-1", metadata=_NoDeepCopy())
    coordinator = _coordinator({"brew_clean_idle": [device]})
    entity = _entity(
        "CraftBeerStyleSensor", coordinator, device.serial_number, "brew_clean_idle"
    )
    assert entity.available
    assert entity.native_value == "IPA"


def test_dataclass_conversion_is_shallow_and_top_level_changes_are_isolated():
    device = Device("serial-1", metadata={"samples": [1, 2, 3]})
    converted = sensor._device_to_dict(device)
    converted["beer_style"] = "Stout"
    assert device.beer_style == " IPA "
    assert converted["metadata"] is device.metadata


def test_lookup_does_not_convert_unrelated_devices(monkeypatch):
    first = Device("serial-1")
    target = Device("serial-2")
    coordinator = _coordinator(
        {"brew_clean_idle": [first], "fermenting": [first, target]}
    )
    convert = Mock(wraps=sensor._device_to_dict)
    monkeypatch.setitem(
        sensor._current_stage_group.__globals__, "_device_to_dict", convert
    )
    assert sensor._current_stage_group(coordinator, "serial-2") == "fermenting"
    convert.assert_not_called()
    assert (
        coordinator.get_merged_device("serial-2", "fermenting")["beer_style"] == " IPA "
    )
    convert.assert_called_once_with(target)


@pytest.mark.parametrize(
    ("class_name", "style", "name", "phase", "group", "expected"),
    [
        ("CraftBeerStyleSensor", " IPA ", "Test beer", None, "brew_clean_idle", "IPA"),
        ("KegBeerStyleSensor", " Stout ", "Test beer", None, "serving", "Stout"),
        ("CraftBeerStyleSensor", " ", "Test beer", None, "brew_clean_idle", None),
        ("KegBeerStyleSensor", None, "Custom Fermentation", None, "fermenting", "N/A"),
        ("CraftBeerStyleSensor", "Unknown", "Unknown", 1, "fermenting", "N/A"),
    ],
)
def test_beer_style_values_preserved(class_name, style, name, phase, group, expected):
    device = Device("serial-1", beer_style=style, beer_name=name, process_phase=phase)
    coordinator = _coordinator({group: [device]})
    entity = _entity(class_name, coordinator, device.serial_number, group)
    assert entity.native_value == expected


def test_mqtt_overlay_and_session_cache_do_not_mutate_rest_device():
    device = Device("serial-1", current_temp=20.0, active_session=42)
    when = datetime(2026, 10, 10, tzinfo=timezone.utc)
    telemetry = SimpleNamespace(current_temperature=19.0, next_action_at=when)
    coordinator = _coordinator(
        {"fermenting": [device]}, telemetry={"serial-1": telemetry}
    )
    coordinator._session_created_by_id[42] = when
    merged = coordinator.get_merged_device("serial-1", "fermenting")
    assert merged["current_temp"] == 19.0
    assert merged["target_temp"] == 18.0
    assert merged["process_estimate_remaining"] == when
    assert merged["session_created"] == when
    assert device.current_temp == 20.0
    assert device.process_estimate_remaining is None
    assert not hasattr(device, "session_created")
    coordinator.realtime.get_telemetry = lambda serial: SimpleNamespace(
        current_temperature=18.5
    )
    assert (
        coordinator.get_merged_device("serial-1", "fermenting")["current_temp"] == 18.5
    )


def test_rest_session_precedence_and_inactive_session_cleanup_preserved():
    device = {
        "serial_number": "serial-1",
        "active_session": 42,
        "session_created": "stale",
    }
    coordinator = _coordinator(
        {"fermenting": [device]}, telemetry={"serial-1": SimpleNamespace(session_id=43)}
    )
    when = datetime(2026, 10, 10, tzinfo=timezone.utc)
    coordinator._active_session_by_serial["serial-1"] = 42
    coordinator._session_created_by_id[42] = when
    assert (
        coordinator.get_merged_device("serial-1", "fermenting")["active_session"] == 42
    )
    coordinator._active_session_by_serial["serial-1"] = None
    device["active_session"] = None
    merged = coordinator.get_merged_device("serial-1", "fermenting")
    assert "active_session" not in merged
    assert "session_created" not in merged
    assert device == {
        "serial_number": "serial-1",
        "active_session": None,
        "session_created": "stale",
    }


def test_missing_devices_and_replaced_overview_are_not_stale():
    coordinator = _coordinator({"brew_clean_idle": [Device("serial-1")]})
    assert coordinator.get_merged_device("missing", "brew_clean_idle") is None
    assert sensor._current_stage_group(coordinator, "missing") == "unknown"
    coordinator.data = SimpleNamespace(
        serving=[{"serial_number": "serial-1", "beer_style": "Lager"}]
    )
    assert sensor._current_stage_group(coordinator, "serial-1") == "serving"
    assert coordinator.get_merged_device("serial-1", "brew_clean_idle")["beer_style"] == "Lager"
    assert coordinator.get_merged_device("serial-1", "serving")["beer_style"] == "Lager"


def test_dictionary_and_object_conversion_remain_supported():
    payload = {"serial_number": "serial-1"}
    assert sensor._device_to_dict(payload) is payload
    assert sensor._device_to_dict(SimpleNamespace(**payload)) == payload
    assert sensor._device_to_dict(None) == {}


def test_dictionary_backed_beer_style_state_read():
    device = {
        "serial_number": "serial-1",
        "beer_style": " Lager ",
        "beer_name": "Test beer",
    }
    coordinator = _coordinator({"brew_clean_idle": [device]})
    entity = _entity("CraftBeerStyleSensor", coordinator, "serial-1", "brew_clean_idle")
    assert entity.available
    assert entity.native_value == "Lager"
    coordinator.last_update_success = False
    assert not entity.available


def test_discovery_and_supplemental_timestamps_preserved():
    device = Device("serial-1")
    overview = SimpleNamespace(brew_clean_idle=[device])
    coordinator = _coordinator({})
    assert sensor._collect_serials(coordinator, overview) == {"serial-1"}
    when = datetime(2026, 10, 10, tzinfo=timezone.utc)
    sensor._merge_device_status_from_devices(
        overview, [{"serial_number": "serial-1", "last_time_online": when}]
    )
    assert device.last_time_online == when


@pytest.mark.parametrize(
    ("action_class", "time_class"),
    [
        ("CraftUserActionRequiredSensor", "CraftNextActionDateTimeSensor"),
        ("KegActionRequiredSensor", "KegNextActionDateTimeSensor"),
    ],
)
@pytest.mark.parametrize("mqtt_action", [None, 0])
def test_device_attention_survives_independent_mqtt_countdown(
    action_class, time_class, mqtt_action
):
    serial = "example-keg"
    overview = SimpleNamespace(
        fermenting=[{
            "serial_number": serial, "user_action": 0,
            "active_session": 100, "online": True,
        }]
    )
    deadline = datetime(2025, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
    sensor._merge_device_status_from_devices(
        overview,
        [{
            "serial_number": serial,
            "current_state": 2,
            "process_type": 4,
            "process_state": 78,
            "user_action": 30,
            "process_estimate_remaining": deadline,
            "text": "Needs your attention",
        }],
    )
    coordinator = _coordinator(
        vars(overview),
        telemetry={serial: SimpleNamespace(
            user_action=mqtt_action,
            next_action_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            seconds_until_next_action=604800,
        )},
    )
    coordinator._session_created_by_id[100] = deadline
    action = _entity(action_class, coordinator, serial, "fermenting")
    next_action = _entity(time_class, coordinator, serial, "fermenting")
    next_action._cached_value = None

    assert action.native_value == "Action required"
    next_value = next_action.native_value
    assert next_value == datetime(2099, 1, 1, tzinfo=timezone.utc)
    merged = coordinator.get_merged_device(serial, "fermenting")
    assert merged["process_state"] == 78
    assert merged["process_estimate_remaining"] == next_value
    assert next_action.native_value == next_value

    sensor._merge_device_status_from_devices(
        overview,
        [{"serial_number": serial, "user_action": 0, "process_estimate_remaining": None}],
    )
    assert action.native_value == "No action required"
    assert next_action.native_value == datetime(2099, 1, 1, tzinfo=timezone.utc)
    coordinator.realtime.get_telemetry = lambda serial: None
    assert next_action.native_value is None


@pytest.mark.parametrize(
    ("action_class", "time_class"),
    [
        ("CraftUserActionRequiredSensor", "CraftNextActionDateTimeSensor"),
        ("KegActionRequiredSensor", "KegNextActionDateTimeSensor"),
    ],
)
def test_anonymized_capture_reports_rest_action_and_mqtt_deadline(action_class, time_class):
    fixture = Path(__file__).parent / "fixtures" / "pending_action_mqtt.json"
    capture = json.loads(fixture.read_text())
    rest_device = capture["rest_device"]
    serial = rest_device["serial_number"]
    overview = SimpleNamespace(fermenting=[{
        "serial_number": serial, "active_session": rest_device["active_session"],
        "online": True,
    }])
    sensor._merge_device_status_from_devices(overview, [rest_device])
    coordinator = _coordinator(vars(overview))
    coordinator._session_created_by_id[rest_device["active_session"]] = datetime(
        2025, 1, 1, tzinfo=timezone.utc
    )
    action = _entity(action_class, coordinator, serial, "fermenting")
    next_action = _entity(time_class, coordinator, serial, "fermenting")
    prompt_class = (
        "CraftRequiredActionSensor" if action_class.startswith("Craft")
        else "KegRequiredActionSensor"
    )
    prompt = _entity(prompt_class, coordinator, serial, "fermenting")
    for raw in capture["mqtt_messages"]:
        message = dict(raw)
        message["next_action_at"] = sensor._coerce_timestamp(message["next_action_at"])
        coordinator.realtime.get_telemetry = lambda serial: SimpleNamespace(**message)
        assert action.available
        assert action.native_value == "Action required"
        assert next_action.available
        assert next_action.native_value == message["next_action_at"].replace(second=0, microsecond=0)
        assert prompt.native_value == "Place trub container"
        assert prompt.unique_id == f"{serial}_required_action"
        for entity in (action, next_action, prompt):
            attrs = entity.extra_state_attributes
            assert attrs["rest_user_action"] == 30
            assert attrs["mqtt_user_action"] == 0
            assert attrs["action_conflict"] is True
            assert attrs["rest_next_action_at"] is None
            assert attrs["mqtt_seconds_until_next_action"] == 604800
            assert attrs["mqtt_next_action_at"] == message["next_action_at"]
            assert attrs["mqtt_session_matches"] is True
            assert attrs["deadline_status"] == "provided"
            assert attrs["required_action"] == "Place trub container"
            assert attrs["action_status"] == "Waiting for user action"
            assert attrs["waiting_since"] is None


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (None, None),
        ({}, None),
        ({"user_action": 0}, "No action required"),
        ({"user_action": 30}, "Place trub container"),
        ({"user_action": 4}, "Add ingredient"),
        ({"user_action": "30"}, "Place trub container"),
        ({"user_action": 999}, "Unknown action (999)"),
        ({"user_action_name": "ACTION_PLACE_TRUB_CONTAINER"}, "Place trub container"),
    ],
)
def test_required_action_description(device, expected):
    assert sensor._required_action_description(device) == expected


@pytest.mark.parametrize("prefix", ["Craft", "Keg"])
def test_action_diagnostics_do_not_reuse_failed_rest_or_previous_session(prefix):
    device = {
        "serial_number": "example-keg", "online": True, "user_action": 30,
        "active_session": 100,
    }
    telemetry = SimpleNamespace(user_action=4, session_id=99)
    coordinator = _coordinator(
        {"fermenting": [device]}, telemetry={"example-keg": telemetry}
    )
    coordinator._session_created_by_id[100] = datetime(2025, 1, 1, tzinfo=timezone.utc)
    entity = _entity(f"{prefix}RequiredActionSensor", coordinator, "example-keg", "fermenting")
    assert entity.native_value == "Place trub container"
    assert entity.extra_state_attributes["mqtt_session_matches"] is False
    assert entity.extra_state_attributes["action_conflict"] is None
    coordinator.last_update_success = False
    assert entity.native_value == "Add ingredient"
    assert entity.extra_state_attributes["rest_user_action"] is None
    assert entity.extra_state_attributes["action_conflict"] is None


@pytest.mark.parametrize("prefix", ["Craft", "Keg"])
def test_required_action_clears_on_rest_refresh_and_mqtt_deadline_resumes(prefix):
    serial = "example-keg"
    deadline = datetime(2025, 1, 8, tzinfo=timezone.utc)
    entered_state = datetime(2025, 1, 1, tzinfo=timezone.utc)
    device = {
        "serial_number": serial, "online": True, "user_action": 30,
        "last_process_state_change": entered_state,
    }
    coordinator = _coordinator(
        {"fermenting": [device]},
        telemetry={serial: SimpleNamespace(user_action=0, next_action_at=deadline)},
    )
    entity = _entity(f"{prefix}RequiredActionSensor", coordinator, serial, "fermenting")
    assert entity.native_value == "Place trub container"
    assert entity.extra_state_attributes["action_status"] == "Waiting for user action"
    assert entity.extra_state_attributes["waiting_since"] == entered_state
    sensor._merge_device_status_from_devices(
        coordinator.data, [{"serial_number": serial, "user_action": 0}]
    )
    assert entity.native_value == "No action required"
    assert entity.extra_state_attributes["action_status"] == "No action required"
    assert entity.extra_state_attributes["waiting_since"] is None
    assert entity.extra_state_attributes["action_conflict"] is False
    assert entity.extra_state_attributes["deadline_status"] == "provided"
    coordinator.realtime.get_telemetry = lambda serial: None
    assert entity.extra_state_attributes["mqtt_user_action"] is None
    assert entity.extra_state_attributes["mqtt_session_matches"] is None


@pytest.mark.parametrize("prefix", ["Craft", "Keg"])
@pytest.mark.parametrize("rest_ok", [False, True])
def test_new_mqtt_action_does_not_inherit_previous_rest_wait_time(prefix, rest_ok):
    serial = "example-keg"
    device = {
        "serial_number": serial, "online": True, "user_action": 30,
        "last_process_state_change": "2025-01-01T00:00:00Z",
    }
    coordinator = _coordinator(
        {"fermenting": [device]},
        telemetry={serial: SimpleNamespace(user_action=4)},
    )
    coordinator.last_update_success = rest_ok
    entity = _entity(f"{prefix}RequiredActionSensor", coordinator, serial, "fermenting")
    assert entity.native_value == "Add ingredient"
    assert entity.extra_state_attributes["action_status"] == "Waiting for user action"
    assert entity.extra_state_attributes["waiting_since"] is None


def test_coordinator_refresh_merges_device_status_before_publishing_overview():
    overview = SimpleNamespace(
        fermenting=[{"serial_number": "serial-1", "user_action": 0}]
    )
    coordinator = _coordinator({})
    coordinator.client = SimpleNamespace(get_brewery_overview=Mock(), get_devices=Mock())
    coordinator.hass.async_add_executor_job = AsyncMock(
        side_effect=[overview, [{"serial_number": "serial-1", "user_action": 30}]]
    )
    coordinator._async_refresh_session_metadata = AsyncMock()
    coordinator.realtime = None

    result = asyncio.run(coordinator._async_update_data())

    assert result is overview
    assert result.fermenting[0]["user_action"] == 30
    coordinator._async_refresh_session_metadata.assert_awaited_once_with(overview)


@pytest.mark.parametrize("use_dataclass", [False, True])
def test_device_status_merge_is_serial_scoped_and_preserves_overview_metadata(use_dataclass):
    device = Device("serial-1") if use_dataclass else {
        "serial_number": "serial-1", "beer_name": "Test beer", "user_action": 30
    }
    overview = SimpleNamespace(fermenting=[device])
    sensor._merge_device_status_from_devices(
        overview,
        [
            {"serial_number": "other", "user_action": 4},
            {
                "serial_number": "serial-1",
                "user_action": 0,
                "process_state": 80,
                "process_estimate_remaining": None,
                "beer_name": None,
            },
        ],
    )
    merged = sensor._device_to_dict(device)
    assert merged["user_action"] == 0
    assert merged["process_state"] == 80
    assert merged["process_estimate_remaining"] is None
    assert merged["beer_name"] == "Test beer"


def test_missing_device_status_fields_do_not_clear_overview():
    device = {"serial_number": "serial-1", "user_action": 30, "text": "Needs your attention"}
    overview = SimpleNamespace(fermenting=[device])
    sensor._merge_device_status_from_devices(
        overview, [{"serial_number": "serial-1", "user_action": None}]
    )
    assert device["user_action"] == 30
    assert device["text"] == "Needs your attention"


def test_slotted_dataclass_device_conversion(monkeypatch):
    @dataclasses.dataclass(slots=True)
    class SlottedDevice:
        serial_number: str
        beer_style: str

    monkeypatch.setitem(sensor._device_to_dict.__globals__, "Device", SlottedDevice)
    assert sensor._device_to_dict(SlottedDevice("serial-1", "IPA")) == {
        "serial_number": "serial-1",
        "beer_style": "IPA",
    }


_SENSOR_CLASSES = [
    name for name, cls in vars(sensor).items()
    if isinstance(cls, type)
    and name not in {"CraftSensor", "KegSensor"}
    and issubclass(cls, (sensor.CraftSensor, sensor.KegSensor))
]
_REST_SENSORS = {
    "CraftBeerStyleSensor", "CraftBeerNameSensor", "CraftSessionIdSensor",
    "CraftSessionStartedSensor", "CraftSensorCurrentStageSensor",
    "KegBeerStyleSensor", "KegBeerNameSensor", "KegSessionIdSensor",
    "KegSessionStartedSensor", "KegCurrentStageSensor",
}
_CONNECTION_SENSORS = {"CraftSensorOnlineStatusSensor", "KegOnlineStatusSensor"}
_REST_DEVICE_SENSORS = {
    "CraftSensorIsUpdatingSensor", "KegIsUpdatingSensor",
    "CraftSensorNeedsCleaningSensor", "KegNeedsCleaningSensor",
}
_MQTT_SENSORS = {
    "CraftProcessPhaseSensor", "KegProcessPhaseSensor",
    "CraftTempControlPowerSensor", "KegTempControlPowerSensor",
    "CraftPeltierFanPowerSensor", "KegPeltierFanPowerSensor",
    "CraftEspCoreTempSensor", "KegEspCoreTempSensor",
    "CraftButtonSensor", "KegButtonSensor",
    "CraftPeltierModeSensor", "KegPeltierModeSensor",
}


@pytest.mark.parametrize("class_name", _SENSOR_CLASSES)
@pytest.mark.parametrize(
    ("rest_ok", "online", "mqtt_ok"),
    [
        (True, True, True),
        (True, False, False),
        (True, None, False),
        (True, True, False),
        (True, False, True),
        (False, True, False),
        (False, False, True),
    ],
)
def test_every_sensor_uses_its_required_source(class_name, rest_ok, online, mqtt_ok):
    device = {"serial_number": "serial-1", "online": online}
    coordinator = _coordinator(
        {"serving": [device]},
        telemetry={"serial-1": SimpleNamespace()} if mqtt_ok else {},
    )
    coordinator.last_update_success = rest_ok
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    if class_name in _REST_SENSORS:
        expected = rest_ok
    elif class_name in _CONNECTION_SENSORS:
        expected = rest_ok or mqtt_ok
    elif class_name in _REST_DEVICE_SENSORS:
        expected = rest_ok and online is True
    elif class_name in _MQTT_SENSORS:
        expected = mqtt_ok
    else:
        expected = not (rest_ok and online is False) and (
            mqtt_ok or (rest_ok and online is True)
        )
    assert entity.available is expected


@pytest.mark.parametrize("class_name", _SENSOR_CLASSES)
def test_every_sensor_unavailable_without_any_source(class_name):
    coordinator = _coordinator({})
    coordinator.last_update_success = False
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert not entity.available


@pytest.mark.parametrize(
    "class_name",
    sorted(set(_SENSOR_CLASSES) - {
        "CraftLastTimeOnlineSensor", "KegLastTimeOnlineSensor",
        "CraftSensorCurrentStageSensor", "KegCurrentStageSensor",
        "CraftSensorOnlineStatusSensor", "KegOnlineStatusSensor",
    }),
)
def test_connected_source_with_missing_value_is_unknown(class_name):
    coordinator = _coordinator(
        {"serving": [{"serial_number": "serial-1", "online": True}]},
        telemetry={"serial-1": SimpleNamespace(measurements={})},
    )
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert entity.available
    assert entity.native_value is None


@pytest.mark.parametrize("class_name", sorted(_CONNECTION_SENSORS))
@pytest.mark.parametrize(("online", "expected"), [(True, "Online"), (False, "Offline"), (None, None)])
def test_cloud_status_does_not_invent_offline_for_missing_status(class_name, online, expected):
    coordinator = _coordinator({"serving": [{"serial_number": "serial-1", "online": online}]})
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert entity.available
    assert entity.native_value == expected


@pytest.mark.parametrize("class_name", ["CraftLastTimeOnlineSensor", "KegLastTimeOnlineSensor"])
def test_online_since_tracks_observed_connection_not_historical_last_seen(class_name, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 17, 10, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setitem(sensor.MiniBrewDataUpdateCoordinator.get_online_since.__globals__, "datetime", Clock)
    device = {"serial_number": "serial-1", "online": True, "last_time_online": Clock.current - timedelta(days=5)}
    coordinator = _coordinator({"serving": [device]})
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    coordinator.async_update_listeners()
    start = entity.native_value
    assert start == Clock.current
    Clock.current += timedelta(minutes=2)
    assert entity.native_value == start
    device["online"] = False
    coordinator.async_update_listeners()
    assert not entity.available
    assert entity.native_value is None
    Clock.current += timedelta(minutes=2)
    device["online"] = True
    coordinator.async_update_listeners()
    assert entity.available
    assert entity.native_value == Clock.current
    assert entity.native_value != start
    assert entity.unique_id == "serial-1_last_time_online"


def test_broker_connection_alone_is_not_device_connectivity():
    coordinator = _coordinator({"serving": [{"serial_number": "serial-1", "online": False}]})
    coordinator.realtime.connected = True
    assert not coordinator.is_device_online("serial-1")
    assert coordinator.get_online_since("serial-1") is None


@pytest.mark.parametrize("class_name", ["CraftNextActionDateTimeSensor", "KegNextActionDateTimeSensor"])
def test_next_action_does_not_pin_overdue_or_cross_session_deadlines(class_name):
    first = datetime(2026, 1, 1, 10, 1, 12, tzinfo=timezone.utc)
    second = first + timedelta(hours=1)
    device = {"serial_number": "serial-1", "online": True, "active_session": 42, "process_estimate_remaining": first}
    coordinator = _coordinator({"fermenting": [device]})
    coordinator._session_created_by_id = {42: first, 43: second}
    entity = _entity(class_name, coordinator, "serial-1", "fermenting")
    assert entity.native_value == first.replace(second=0)
    device.update(active_session=43, process_estimate_remaining=second)
    assert entity.native_value == second.replace(second=0)
    device.update(active_session=None, process_estimate_remaining=None)
    assert entity.native_value is None
    coordinator.data = SimpleNamespace()
    assert entity.native_value is None


@pytest.mark.parametrize("class_name", ["CraftNextActionDateTimeSensor", "KegNextActionDateTimeSensor"])
def test_idle_deadline_is_cleared_but_legitimate_sessionless_action_is_kept(class_name):
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    device = {"serial_number": "serial-1", "online": True, "active_session": None, "user_action": 0, "process_estimate_remaining": when}
    coordinator = _coordinator({"brew_clean_idle": [device]})
    entity = _entity(class_name, coordinator, "serial-1", "brew_clean_idle")
    assert entity.native_value is None
    device["user_action"] = 5
    assert entity.native_value == when


@pytest.mark.parametrize("session_id", [None, 43])
def test_old_session_mqtt_cannot_restore_action_after_reset_or_new_session(session_id):
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    device = {"serial_number": "serial-1", "online": True, "active_session": session_id, "user_action": 0, "process_estimate_remaining": None}
    coordinator = _coordinator(
        {"serving": [device]},
        telemetry={"serial-1": SimpleNamespace(session_id=42, user_action=5, next_action_at=when, current_temperature=19)},
    )
    coordinator._session_created_by_id[43] = when
    merged = coordinator.get_merged_device("serial-1", "serving")
    assert merged["user_action"] == 0
    assert merged["process_estimate_remaining"] is None
    assert merged.get("active_session") == session_id
    assert merged["current_temp"] == 19


def test_fresh_mqtt_does_not_make_stale_rest_readings_current():
    device = {"serial_number": "serial-1", "online": False, "current_temp": 20, "user_action": 5}
    coordinator = _coordinator(
        {"serving": [device]}, telemetry={"serial-1": SimpleNamespace(target_temperature=18)}
    )
    merged = coordinator.get_merged_device("serial-1", "serving")
    assert merged.get("current_temp") is None
    assert merged.get("user_action") is None
    assert merged["target_temp"] == 18
    coordinator.last_update_success = False
    merged = coordinator.get_merged_device("serial-1", "serving")
    assert merged.get("current_temp") is None
    assert merged["target_temp"] == 18


@pytest.mark.parametrize("class_name", ["CraftPeltierFanPowerSensor", "KegPeltierFanPowerSensor", "CraftEspCoreTempSensor", "KegEspCoreTempSensor", "CraftButtonSensor", "KegButtonSensor"])
def test_valid_zero_measurements_are_not_hidden(class_name):
    telemetry = SimpleNamespace(measurements={kind: 0.0 for kind in SensorType})
    coordinator = _coordinator({}, telemetry={"serial-1": telemetry})
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert entity.available
    assert entity.native_value == ("Off" if "Button" in class_name else 0)


@pytest.mark.parametrize("class_name", ["CraftTempControlPowerSensor", "KegTempControlPowerSensor", "CraftPeltierModeSensor", "KegPeltierModeSensor"])
def test_zero_peltier_power_is_valid_idle_state(class_name):
    coordinator = _coordinator({}, telemetry={"serial-1": SimpleNamespace(temp_control_power=0)})
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert entity.available
    assert entity.native_value == ("Idle" if "Mode" in class_name else 0)


@pytest.mark.parametrize("class_name", ["CraftUserActionRequiredSensor", "KegActionRequiredSensor"])
def test_action_clear_reaches_entity_state(class_name):
    device = {"serial_number": "serial-1", "online": True, "user_action": 5}
    coordinator = _coordinator(
        {"serving": [device]}, telemetry={"serial-1": SimpleNamespace(user_action=0)}
    )
    entity = _entity(class_name, coordinator, "serial-1", "serving")
    assert entity.available
    assert entity.native_value == "Action required"
    sensor._merge_device_status_from_devices(
        coordinator.data, [{"serial_number": "serial-1", "user_action": 0}]
    )
    assert entity.native_value == "No action required"
    coordinator.realtime.get_telemetry = lambda serial: None
    device["user_action"] = None
    assert entity.native_value is None
    assert entity.icon == "mdi:help-circle-outline"


def test_expiry_updates_device_availability_and_online_period_without_rest_poll():
    device = {"serial_number": "serial-1", "online": None}
    coordinator = _coordinator({"serving": [device]})
    loop = SimpleNamespace(
        call_soon_threadsafe=lambda callback: callback(),
        call_later=Mock(return_value=Mock()),
    )
    manager = sensor.MiniBrewRealtimeManager(SimpleNamespace(loop=loop), coordinator, None)
    manager._online_serials = {"serial-1"}
    coordinator.realtime = manager
    manager._connected = True
    manager._handle_device_log(SimpleNamespace(device_uuid="serial-1", current_temperature=19))
    entity = _entity("KegCurrentTemperatureSensor", coordinator, "serial-1", "serving")
    other = _entity("KegCurrentTemperatureSensor", coordinator, "serial-2", "serving")
    cloud = _entity("KegOnlineStatusSensor", coordinator, "serial-1", "serving")
    start = coordinator.get_online_since("serial-1")
    assert start is not None
    assert entity.available
    assert entity.native_value == 19
    assert not other.available
    assert cloud.available
    assert cloud.native_value == "Online"
    manager._last_received["serial-1"] -= timedelta(seconds=60)
    manager._async_expire_telemetry("serial-1")
    assert "serial-1" not in coordinator._online_since
    assert not entity.available
    assert cloud.available
    assert cloud.native_value is None
    manager._handle_device_log(SimpleNamespace(device_uuid="serial-1", current_temperature=19))
    assert entity.available
    assert coordinator.get_online_since("serial-1") > start


def test_failed_rest_cannot_suppress_live_deadline_using_an_old_idle_stage():
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    coordinator = _coordinator(
        {"brew_clean_idle": [{"serial_number": "serial-1", "online": False}]},
        telemetry={"serial-1": SimpleNamespace(next_action_at=when, user_action=0)},
    )
    coordinator.last_update_success = False
    entity = _entity("CraftNextActionDateTimeSensor", coordinator, "serial-1", "brew_clean_idle")
    assert entity.available
    assert entity.native_value == when


@pytest.mark.parametrize("connected", [False, True])
def test_mqtt_diagnostic_stays_available_when_cloud_fails(connected):
    path = _ROOT / "binary_sensor.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    namespace = {
        "BinarySensorEntity": object,
        "BinarySensorDeviceClass": SimpleNamespace(CONNECTIVITY="connectivity"),
        "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
    }
    exec(compile(tree, str(path), "exec"), namespace)
    cls = namespace["MiniBrewRealtimeConnectedSensor"]
    entity = cls.__new__(cls)
    entity._coordinator = SimpleNamespace(
        last_update_success=False, realtime=SimpleNamespace(connected=connected)
    )
    assert entity.available
    assert entity.is_on is connected


@pytest.mark.parametrize("online", [True, False, None])
@pytest.mark.parametrize("process_type", [None, 0, 1, 2, 3, 4, 5, 6, 99, "invalid"])
def test_rest_refresh_controls_mqtt_gate(online, process_type):
    import asyncio
    from unittest.mock import AsyncMock

    overview = SimpleNamespace(serving=[{
        "serial_number": "serial-1", "online": online,
        "process_type": process_type, "active_session": 42,
    }])
    coordinator = _coordinator({})
    coordinator.client = SimpleNamespace(
        get_brewery_overview=Mock(return_value=overview),
        get_devices=Mock(return_value=[]),
    )
    coordinator.realtime = SimpleNamespace(async_set_online_devices=AsyncMock())
    coordinator._async_refresh_session_metadata = AsyncMock()

    async def executor(func, *args):
        return func(*args)

    coordinator.hass.async_add_executor_job = executor
    assert asyncio.run(coordinator._async_update_data()) is overview
    coordinator.realtime.async_set_online_devices.assert_awaited_once_with(
        {"serial-1"} if online is True and process_type in {1, 2, 3, 4, 5, 6} else set()
    )


def test_mqtt_gate_ignores_stale_session_and_stage_and_supports_sessionless_cleaning():
    overview = SimpleNamespace(
        fermenting=[{
            "serial_number": "idle", "online": True,
            "process_type": 0, "active_session": 42,
        }],
        brew_clean_idle=[{
            "serial_number": "cleaning", "online": True,
            "process_type": 2, "active_session": None,
        }],
        serving=[{
            "serial_number": "offline", "online": False,
            "process_type": 5, "active_session": 43,
        }],
    )
    assert sensor._mqtt_serials(overview) == {"cleaning"}
    assert sensor._mqtt_serials(None) == set()
    overview.brew_clean_idle[0]["process_type"] = 0
    assert sensor._mqtt_serials(overview) == set()


@pytest.mark.parametrize("prefix", ["Craft", "Keg"])
def test_portal_idle_response_cannot_restore_ended_session_from_caches(prefix):
    device = {
        "serial_number": "serial-1", "online": True, "stage": "Idle",
        "process_type": 0, "session_id": None, "user_action": 0,
        "target_temp": None, "current_temp": None, "beer_name": None,
        "beer_style": None,
    }
    when = datetime(2026, 10, 17, tzinfo=timezone.utc)
    coordinator = _coordinator(
        {"brew_clean_idle": [device]},
        telemetry={"serial-1": SimpleNamespace(
            session_id=82991, process_phase=0, user_action=5,
            next_action_at=when, target_temperature=18, current_temperature=19.9,
        )},
    )
    coordinator._active_session_by_serial["serial-1"] = 82991
    coordinator._session_created_by_serial["serial-1"] = when
    coordinator._session_created_by_id[82991] = when
    # The entity was originally created while fermenting.
    for name in (f"{prefix}SessionIdSensor", f"{prefix}SessionStartedSensor"):
        entity = _entity(name, coordinator, "serial-1", "fermenting")
        assert entity.available
        assert entity.native_value is None
    merged = coordinator.get_merged_device("serial-1", "fermenting")
    assert merged["user_action"] == 0
    assert merged["target_temp"] is None
    assert merged.get("process_estimate_remaining") is None
    assert sensor._current_stage_group(coordinator, "serial-1") == "brew_clean_idle"
    assert sensor._mqtt_serials(coordinator.data) == set()
    coordinator.hass.async_create_task.assert_not_called()


def test_session_lookup_finishing_after_reset_does_not_restore_device_cache():
    import asyncio

    async def scenario():
        coordinator = _coordinator({})
        coordinator._session_lookup_inflight = set()
        coordinator._active_session_by_serial["serial-1"] = 82991
        when = datetime(2026, 10, 17, tzinfo=timezone.utc)

        async def fetch(session_id):
            coordinator._active_session_by_serial["serial-1"] = None
            coordinator._session_created_by_serial.pop("serial-1", None)
            return when

        coordinator._async_fetch_session_started = fetch
        await coordinator._async_ensure_session_created("serial-1", 82991)
        assert "serial-1" not in coordinator._session_created_by_serial
        assert coordinator._session_lookup_inflight == set()
        assert coordinator._session_created_by_id[82991] == when

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [ConnectionError("network down"), PermissionError("401 unauthorized")])
def test_failed_rest_refresh_pauses_mqtt_before_raising(error):
    import asyncio
    from unittest.mock import AsyncMock

    coordinator = _coordinator({})
    coordinator.client = SimpleNamespace(get_brewery_overview=Mock(side_effect=error))
    coordinator.realtime = SimpleNamespace(async_set_online_devices=AsyncMock())

    async def executor(func, *args):
        return func(*args)

    coordinator.hass.async_add_executor_job = executor
    with pytest.raises((RuntimeError, PermissionError)):
        asyncio.run(coordinator._async_update_data())
    coordinator.realtime.async_set_online_devices.assert_awaited_once_with(set())
