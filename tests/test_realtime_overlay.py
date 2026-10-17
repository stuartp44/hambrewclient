"""Unit tests for the MQTT telemetry overlay.

These load ``realtime.py`` directly by path so they run without a Home Assistant
runtime (the module imports only the standard library). Run with pytest, or
directly: ``python3 tests/test_realtime_overlay.py``.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_REALTIME_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "minibrew" / "realtime.py"
_spec = importlib.util.spec_from_file_location("minibrew_realtime", _REALTIME_PATH)
realtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(realtime)


def _telemetry(**kwargs):
    """Build a DeviceLogMessage-like stand-in with the overlaid attributes."""
    defaults = {
        "current_state": None,
        "process_type": None,
        "process_state": None,
        "current_temperature": None,
        "target_temperature": None,
        "user_action": None,
        "process_phase": None,
        "session_id": None,
        "next_action_at": None,
        "seconds_until_next_action": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_none_telemetry_leaves_device_untouched():
    device = {"current_temp": 18.0, "target_temp": 19.0}
    realtime.overlay_mqtt(device, None)
    assert device == {"current_temp": 18.0, "target_temp": 19.0}


def test_non_none_values_win_over_rest():
    device = {"current_temp": 18.0, "target_temp": 19.0, "user_action": 0}
    realtime.overlay_mqtt(
        device,
        _telemetry(current_temperature=19.2, target_temperature=19.0, user_action=3),
    )
    assert device["current_temp"] == 19.2
    assert device["target_temp"] == 19.0
    assert device["user_action"] == 3


def test_none_fields_do_not_clobber_rest():
    device = {"current_temp": 18.0, "target_temp": 19.0}
    # Only current_temperature is present; target must be preserved from REST.
    realtime.overlay_mqtt(device, _telemetry(current_temperature=20.5))
    assert device["current_temp"] == 20.5
    assert device["target_temp"] == 19.0


def test_next_action_at_maps_to_process_estimate_remaining():
    from datetime import datetime, timezone

    when = datetime(2026, 8, 4, 16, 37, 6, tzinfo=timezone.utc)
    device = {"process_estimate_remaining": None}
    realtime.overlay_mqtt(device, _telemetry(next_action_at=when))
    assert device["process_estimate_remaining"] == when


def test_user_action_zero_clears_non_zero_rest():
    device = {"user_action": 5}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 0


def test_missing_user_action_preserves_rest():
    device = {"user_action": 5}
    realtime.overlay_mqtt(device, _telemetry(user_action=None))
    assert device["user_action"] == 5


def test_user_action_zero_applies_when_rest_is_empty_or_zero():
    device = {"user_action": 0}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 0


def test_process_phase_is_mapped_to_device():
    device = {"process_phase": 0}
    realtime.overlay_mqtt(device, _telemetry(process_phase=12))
    assert device["process_phase"] == 12


def test_seconds_until_next_action_maps_to_remaining_seconds():
    device = {"process_estimate_remaining_seconds": 1200}
    realtime.overlay_mqtt(device, _telemetry(seconds_until_next_action=420))
    assert device["process_estimate_remaining_seconds"] == 420


def test_core_process_fields_are_mapped_from_mqtt():
    device = {
        "current_state": 0,
        "process_type": 0,
        "process_state": 0,
        "active_session": None,
    }
    realtime.overlay_mqtt(
        device,
        _telemetry(current_state=2, process_type=4, process_state=80, session_id=80851),
    )
    assert device["current_state"] == 2
    assert device["process_type"] == 4
    assert device["process_state"] == 80
    assert device["active_session"] == 80851


def test_realtime_manager_skips_duplicate_packets():
    calls = []

    class _Loop:
        def call_soon_threadsafe(self, callback):
            calls.append(callback)

    coordinator = SimpleNamespace(async_update_listeners=lambda: None)
    hass = SimpleNamespace(loop=_Loop())
    manager = realtime.MiniBrewRealtimeManager(hass, coordinator, client=None)

    msg = SimpleNamespace(
        device_uuid="serial-1",
        current_state=2,
        process_type=2,
        process_state=101,
        current_temperature=20.0,
        target_temperature=19.0,
        user_action=2,
        process_phase=8,
        session_id=80885,
        next_action_at=None,
        seconds_until_next_action=120,
        temp_control_power=45.0,
        measurements={},
    )

    manager._handle_device_log(msg)
    manager._handle_device_log(msg)

    assert calls.count(coordinator.async_update_listeners) == 1


def test_realtime_manager_ignores_countdown_only_changes():
    from datetime import datetime, timezone

    calls = []

    class _Loop:
        def call_soon_threadsafe(self, callback):
            calls.append(callback)

    coordinator = SimpleNamespace(async_update_listeners=lambda: None)
    hass = SimpleNamespace(loop=_Loop())
    manager = realtime.MiniBrewRealtimeManager(hass, coordinator, client=None)

    common = {
        "device_uuid": "serial-1",
        "current_state": 2,
        "process_type": 2,
        "process_state": 101,
        "current_temperature": 20.0,
        "target_temperature": 19.0,
        "user_action": 2,
        "process_phase": 8,
        "session_id": 80885,
        "temp_control_power": 45.0,
        "measurements": {},
    }

    first = SimpleNamespace(
        **common,
        next_action_at=datetime(2026, 7, 26, 18, 26, 4, tzinfo=timezone.utc),
        seconds_until_next_action=120,
    )
    second = SimpleNamespace(
        **common,
        next_action_at=datetime(2026, 7, 26, 18, 26, 44, tzinfo=timezone.utc),
        seconds_until_next_action=119,
    )

    manager._handle_device_log(first)
    manager._handle_device_log(second)

    assert calls.count(coordinator.async_update_listeners) == 1


def test_realtime_manager_disconnects_stale_client_before_reconnect():
    import asyncio

    class _FakeMqtt:
        def __init__(self, name):
            self.name = name
            self.disconnected = 0
            self.connected = 0

        def on_device_log(self, *args, **kwargs):
            pass

        def on_connected(self, *args, **kwargs):
            pass

        def on_disconnected(self, *args, **kwargs):
            pass

        def on_error(self, *args, **kwargs):
            pass

        def connect(self):
            self.connected += 1

        def disconnect(self):
            self.disconnected += 1

    class _FakeLoop:
        def call_soon_threadsafe(self, callback):
            callback()

    class _FakeHass:
        def __init__(self):
            self.loop = _FakeLoop()

        async def async_add_executor_job(self, func, *args):
            return func(*args)

    class _FakeCoordinator:
        def async_update_listeners(self):
            return None

    existing = _FakeMqtt("old")
    new_client = _FakeMqtt("new")
    hass = _FakeHass()
    coordinator = _FakeCoordinator()
    client = SimpleNamespace(create_mqtt_client=lambda: new_client)
    manager = realtime.MiniBrewRealtimeManager(hass, coordinator, client)
    manager._mqtt = existing

    asyncio.run(manager._async_do_connect())

    assert existing.disconnected == 1
    assert manager._mqtt is new_client
    assert new_client.connected == 1


def _manager_with_device():
    from unittest.mock import Mock

    loop = SimpleNamespace(
        call_soon_threadsafe=lambda callback: callback(),
        call_later=Mock(return_value=Mock()),
    )
    coordinator = SimpleNamespace(async_update_listeners=Mock())
    manager = realtime.MiniBrewRealtimeManager(
        SimpleNamespace(loop=loop), coordinator, client=None
    )
    manager._connected = True
    manager._handle_device_log(_telemetry(device_uuid="serial-1", user_action=5))
    return manager, coordinator


def test_disconnect_clears_telemetry_and_reconnect_does_not_resurrect_it():
    manager, _ = _manager_with_device()
    assert manager.get_telemetry("serial-1").user_action == 5
    manager._stopped = True  # Do not schedule a real reconnect in this unit test.
    manager._handle_disconnected()
    assert manager.get_telemetry("serial-1") is None
    assert manager._telemetry == {}
    assert manager._fingerprints == {}
    manager._connected = True
    assert manager.get_telemetry("serial-1") is None
    manager._stopped = False
    manager._handle_device_log(_telemetry(device_uuid="serial-1", user_action=0))
    assert manager.get_telemetry("serial-1").user_action == 0


def test_telemetry_expires_at_exact_freshness_limit():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    manager, coordinator = _manager_with_device()
    received = datetime(2026, 10, 17, tzinfo=timezone.utc)
    manager._last_received["serial-1"] = received
    with patch.object(realtime, "datetime") as clock:
        clock.now.return_value = received + timedelta(seconds=59, milliseconds=999)
        assert manager.get_telemetry("serial-1") is not None
        clock.now.return_value = received + timedelta(seconds=60)
        assert manager.get_telemetry("serial-1") is None
        calls = coordinator.async_update_listeners.call_count
        manager._async_expire_telemetry("serial-1")
        assert coordinator.async_update_listeners.call_count == calls + 1
    assert manager._telemetry == {}


def test_identical_packets_refresh_freshness_without_state_churn():
    from datetime import timedelta

    manager, coordinator = _manager_with_device()
    manager._last_received["serial-1"] -= timedelta(seconds=59)
    old_received = manager._last_received["serial-1"]
    calls = coordinator.async_update_listeners.call_count
    manager._handle_device_log(_telemetry(device_uuid="serial-1", user_action=5))
    assert manager._last_received["serial-1"] > old_received
    assert coordinator.async_update_listeners.call_count == calls
    assert manager.hass.loop.call_later.call_args.args[0] == 60


def test_invalid_packet_cannot_refresh_telemetry():
    manager, _ = _manager_with_device()
    received = manager._last_received["serial-1"]
    manager._handle_device_log(
        _telemetry(device_uuid="serial-1", user_action=0, decode_error="bad payload")
    )
    assert manager._last_received["serial-1"] == received
    assert manager.get_telemetry("serial-1").user_action == 5


def test_expiry_callback_cannot_delete_a_packet_received_before_rescheduling():
    manager, coordinator = _manager_with_device()
    calls = coordinator.async_update_listeners.call_count
    manager._async_expire_telemetry("serial-1")
    assert manager.get_telemetry("serial-1") is not None
    assert coordinator.async_update_listeners.call_count == calls
    assert manager.hass.loop.call_later.call_args.args[0] > 0


def test_identical_packet_after_silence_notifies_recovery():
    from datetime import timedelta

    manager, coordinator = _manager_with_device()
    manager._last_received["serial-1"] -= timedelta(seconds=60)
    assert manager.get_telemetry("serial-1") is None
    calls = coordinator.async_update_listeners.call_count
    manager._handle_device_log(_telemetry(device_uuid="serial-1", user_action=5))
    assert manager.get_telemetry("serial-1") is not None
    assert coordinator.async_update_listeners.call_count == calls + 1

if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:  # noqa: PERF203
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)
