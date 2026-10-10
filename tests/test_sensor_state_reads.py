"""Exercise the actual sensor read methods without a Home Assistant runtime.

Only the relevant definitions are compiled from the source AST, so the CI test
environment needs no Home Assistant or cloud client dependencies.
"""

import ast
import dataclasses
import importlib.util
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "minibrew"


@dataclasses.dataclass
class Device:
    serial_number: str
    beer_style: str | None = " IPA "
    beer_name: str | None = "Test beer"
    current_temp: float = 20.0
    target_temp: float = 18.0
    process_estimate_remaining: datetime | None = None
    active_session: int | None = None
    process_phase: int | None = None
    metadata: object = None


class ProcessPhase(IntEnum):
    PHASE_NONE = 0
    FERM_PRIMARY = 1


def _load_sensor_reads():
    realtime_spec = importlib.util.spec_from_file_location(
        "state_read_realtime", _ROOT / "realtime.py"
    )
    realtime = importlib.util.module_from_spec(realtime_spec)
    realtime_spec.loader.exec_module(realtime)
    names = {
        "_device_to_dict",
        "_device_serial",
        "_coerce_session_id",
        "_session_id_from_device_dict",
        "_overlay_mqtt",
        "_current_stage_group",
        "_process_phase_display",
        "_is_custom_fermentation_mode",
        "_collect_serials",
        "_merge_last_time_online_from_devices",
        "MiniBrewDataUpdateCoordinator",
        "CraftSensor",
        "CraftBeerStyleSensor",
        "CraftBeerNameSensor",
        "KegSensor",
        "KegBeerStyleSensor",
        "KegBeerNameSensor",
    }
    path = _ROOT / "sensor.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    tree.body = [node for node in tree.body if getattr(node, "name", None) in names]
    namespace = {
        "Device": Device,
        "ProcessPhase": ProcessPhase,
        "fields": dataclasses.fields,
        "is_dataclass": dataclasses.is_dataclass,
        "SensorEntity": object,
        "DataUpdateCoordinator": object,
        "overlay_mqtt": realtime.overlay_mqtt,
        "DOMAIN": "minibrew",
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


sensor = _load_sensor_reads()


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
    merged = coordinator.get_merged_device("serial-1", "fermenting")
    assert "active_session" not in merged
    assert "session_created" not in merged
    assert device == {
        "serial_number": "serial-1",
        "active_session": 42,
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
    assert coordinator.get_merged_device("serial-1", "brew_clean_idle") is None
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
    sensor._merge_last_time_online_from_devices(
        overview, [{"serial_number": "serial-1", "last_time_online": when}]
    )
    assert device.last_time_online == when


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
