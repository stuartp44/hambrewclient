"""Unit tests for the MQTT telemetry overlay.

These load ``realtime.py`` directly by path so they run without a Home Assistant
runtime (the module imports only the standard library). Run with pytest, or
directly: ``python3 tests/test_realtime_overlay.py``.
"""

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

_REALTIME_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "minibrew" / "realtime.py"
_spec = importlib.util.spec_from_file_location("minibrew_realtime", _REALTIME_PATH)
realtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(realtime)


def test_anonymized_capture_preserves_rest_action_and_uses_mqtt_deadline():
    fixture = Path(__file__).parent / "fixtures" / "pending_action_mqtt.json"
    capture = json.loads(fixture.read_text())
    rest_device = capture["rest_device"]
    timestamps = []
    deadlines = []
    for raw in capture["mqtt_messages"]:
        message = dict(raw)
        for field in ("device_timestamp", "next_action_at"):
            message[field] = datetime.fromisoformat(message[field].replace("Z", "+00:00"))
        assert message["session_id"] == rest_device["active_session"]
        assert message["device_uuid"] == rest_device["serial_number"]
        assert message["process_state"] == rest_device["process_state"] == 78
        assert message["user_action"] == 0
        assert message["seconds_until_next_action"] == 604800
        assert message["next_action_at"] - message["device_timestamp"] == timedelta(days=7)
        timestamps.append(message["device_timestamp"])
        deadlines.append(message["next_action_at"])

        device = dict(rest_device)
        realtime.overlay_mqtt(device, SimpleNamespace(**message))
        assert device["user_action"] == 30
        assert device["process_estimate_remaining"] == message["next_action_at"]
        assert device["process_estimate_remaining_seconds"] == 604800

    assert timestamps[1] - timestamps[0] == timedelta(seconds=29.020)
    assert deadlines[1] - deadlines[0] == timestamps[1] - timestamps[0]


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


def test_user_action_zero_preserves_pending_rest_action():
    device = {"user_action": 5}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 5


def test_mqtt_process_prompt_wins_when_action_code_is_undefined():
    device = {"user_action": 0}
    realtime.overlay_mqtt(device, _telemetry(user_action=0, process_state=78))
    assert device["user_action"] == 30
    assert device["action_source"] == "mqtt_process_state"


def test_mqtt_process_transition_clears_completed_prompt():
    device = {"user_action": 30}
    realtime.overlay_mqtt(device, _telemetry(user_action=0, process_state=80))
    assert device["user_action"] == 0
    assert device["action_source"] == "mqtt_process_state"


def test_unknown_mqtt_process_uses_rest_instead_of_guessing():
    device = {"user_action": 38}
    realtime.overlay_mqtt(device, _telemetry(user_action=0, process_state=999))
    assert device["user_action"] == 38
    assert device["action_source"] == "rest"


def test_nonzero_mqtt_action_has_priority_over_process_state():
    device = {"user_action": 30}
    realtime.overlay_mqtt(device, _telemetry(user_action=4, process_state=78))
    assert device["user_action"] == 4
    assert device["action_source"] == "mqtt"


def test_different_session_cannot_derive_a_prompt_from_mqtt_process():
    device = {"user_action": 0}
    realtime.overlay_mqtt(
        device, _telemetry(user_action=0, process_state=78), include_session=False
    )
    assert device["user_action"] == 0
    assert device["action_source"] == "rest"


def test_missing_user_action_preserves_rest():
    device = {"user_action": 5}
    realtime.overlay_mqtt(device, _telemetry(user_action=None))
    assert device["user_action"] == 5


def test_user_action_zero_applies_when_rest_is_empty_or_zero():
    device = {"user_action": 0}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 0


def test_pending_rest_action_does_not_suppress_mqtt_countdown():
    from datetime import datetime, timezone

    deadline = datetime(2026, 10, 18, 8, 54, tzinfo=timezone.utc)
    for action in (None, 0):
        device = {"user_action": 30, "process_estimate_remaining": deadline}
        realtime.overlay_mqtt(
            device,
            _telemetry(
                user_action=action,
                next_action_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
                seconds_until_next_action=604800,
                current_temperature=18.1,
            ),
        )
        assert device["user_action"] == 30
        assert device["process_estimate_remaining"] == datetime(2026, 10, 25, tzinfo=timezone.utc)
        assert device["process_estimate_remaining_seconds"] == 604800
        assert device["current_temp"] == 18.1


def test_mqtt_action_with_context_can_update_rest_deadline():
    from datetime import datetime, timezone

    deadline = datetime(2026, 10, 18, 8, 54, tzinfo=timezone.utc)
    device = {"user_action": 30, "process_estimate_remaining": None}
    realtime.overlay_mqtt(
        device, _telemetry(user_action=30, next_action_at=deadline, seconds_until_next_action=60)
    )
    assert device["process_estimate_remaining"] == deadline
    assert device["process_estimate_remaining_seconds"] == 60


def test_pending_action_uses_rest_deadline_when_mqtt_omits_countdown():
    deadline = datetime(2025, 1, 1).astimezone()
    device = {"user_action": 30, "process_estimate_remaining": deadline}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 30
    assert device["process_estimate_remaining"] == deadline


def test_mismatched_session_cannot_replace_rest_action_or_deadline():
    deadline = datetime(2025, 1, 1).astimezone()
    device = {"user_action": 30, "process_estimate_remaining": deadline}
    realtime.overlay_mqtt(
        device,
        _telemetry(
            user_action=0, next_action_at=datetime(2099, 1, 1).astimezone(),
            seconds_until_next_action=604800,
        ),
        include_session=False,
    )
    assert device["user_action"] == 30
    assert device["process_estimate_remaining"] == deadline
    assert "process_estimate_remaining_seconds" not in device


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
    manager._online_serials = {"serial-1"}

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
    manager._online_serials = {"serial-1"}

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
    manager._online_serials = {"serial-1"}
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
    manager._online_serials = {"serial-1"}
    manager._connected = True
    manager._handle_device_log(_telemetry(device_uuid="serial-1", user_action=5))
    return manager, coordinator


def test_stop_releases_all_telemetry_and_timer_references():
    import asyncio

    manager, _ = _manager_with_device()
    handle = manager._expiry_handles["serial-1"]
    asyncio.run(manager.async_stop())
    handle.cancel.assert_called_once()
    for name in ("_telemetry", "_fingerprints", "_last_received", "_last_update", "_expiry_handles"):
        assert getattr(manager, name) == {}


def test_removed_devices_do_not_accumulate_in_realtime_caches():
    import asyncio
    from unittest.mock import Mock

    manager, _ = _manager_with_device()
    manager._mqtt = Mock()
    manager.hass.async_add_executor_job = None
    manager._subscribed = {"serial-1"}

    async def executor(func, *args):
        return func(*args)

    manager.hass.async_add_executor_job = executor
    asyncio.run(manager.async_set_online_devices({"replacement"}))
    for name in ("_telemetry", "_fingerprints", "_last_received", "_last_update", "_expiry_handles"):
        assert "serial-1" not in getattr(manager, name)


def test_10000_packets_retain_only_latest_message_and_release_on_stop():
    import asyncio
    import gc
    import weakref

    class Message:
        def __init__(self, index):
            self.device_uuid = "example-keg"
            self.user_action = index % 2
            self.payload = bytes(8192)

    async def scenario():
        coordinator = SimpleNamespace(async_update_listeners=lambda: None)
        manager = realtime.MiniBrewRealtimeManager(
            SimpleNamespace(loop=asyncio.get_running_loop()), coordinator, client=None
        )
        manager._online_serials = {"example-keg"}
        manager._connected = True
        refs = []
        for batch in range(20):
            for index in range(500):
                message = Message(batch * 500 + index)
                refs.append(weakref.ref(message))
                manager._handle_device_log(message)
            del message
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            gc.collect()
            assert sum(ref() is not None for ref in refs) == 1
            assert len(manager._telemetry) == 1
            assert len(manager._expiry_handles) == 1
        await manager.async_stop()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        assert all(ref() is None for ref in refs)
        assert manager._expiry_handles == {}
        manager_ref = weakref.ref(manager)
        del manager
        gc.collect()
        assert manager_ref() is None

    asyncio.run(scenario())


def test_100_start_stop_cycles_release_mqtt_clients_and_managers():
    import asyncio
    import gc
    import weakref

    refs = []
    disconnected = []

    class Mqtt:
        def on_device_log(self, callback):
            self.log_callback = callback

        def on_connected(self, callback):
            self.connected_callback = callback

        def on_disconnected(self, callback):
            self.disconnected_callback = callback

        def on_error(self, callback):
            self.error_callback = callback

        def connect(self):
            pass

        def disconnect(self):
            disconnected.append(True)

    def create_client():
        client = Mqtt()
        refs.append(weakref.ref(client))
        return client

    async def executor(func, *args):
        return func(*args)

    async def scenario():
        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(), async_add_executor_job=executor
        )
        coordinator = SimpleNamespace(async_update_listeners=lambda: None)
        for _ in range(100):
            manager = realtime.MiniBrewRealtimeManager(
                hass, coordinator, SimpleNamespace(create_mqtt_client=create_client)
            )
            refs.append(weakref.ref(manager))
            await manager.async_start({"example-keg"})
            await manager.async_stop()
            del manager
            await asyncio.sleep(0)
            gc.collect()
            assert all(ref() is None for ref in refs)
        assert len(disconnected) == 100

    asyncio.run(scenario())


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


def test_rest_offline_prevents_startup_and_rest_online_resumes_connection():
    import asyncio
    from unittest.mock import Mock

    async def scenario():
        mqtt = Mock()
        client = SimpleNamespace(create_mqtt_client=Mock(return_value=mqtt))

        async def executor(func, *args):
            return func(*args)

        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            async_add_executor_job=executor,
            async_create_task=asyncio.create_task,
        )
        coordinator = SimpleNamespace(async_update_listeners=Mock())
        manager = realtime.MiniBrewRealtimeManager(hass, coordinator, client)
        await manager.async_start(set())
        client.create_mqtt_client.assert_not_called()
        manager._schedule_reconnect()
        await asyncio.sleep(0)
        assert manager._reconnect_task is None

        await manager.async_set_online_devices({"online-device"})
        client.create_mqtt_client.assert_called_once()
        mqtt.connect.assert_called_once()
        mqtt.subscribe_device_logs.assert_called_once_with("online-device")
        manager.async_ensure_subscribed({"offline-device"})
        mqtt.subscribe_device_logs.assert_called_once()

        manager._connected = True
        manager._handle_device_log(_telemetry(device_uuid="online-device", user_action=5))
        await asyncio.sleep(0)
        assert manager.get_telemetry("online-device") is not None
        await manager.async_set_online_devices(set())
        await asyncio.sleep(0)
        mqtt.disconnect.assert_called_once()
        assert manager._mqtt is None
        assert not manager.connected
        assert manager.get_telemetry("online-device") is None
        assert manager._subscribed == set()
        manager._handle_connected()
        assert not manager.connected
        await manager._async_do_connect()
        client.create_mqtt_client.assert_called_once()

        await manager.async_set_online_devices({"online-device"})
        assert client.create_mqtt_client.call_count == 2
        assert mqtt.connect.call_count == 2
        await manager.async_stop()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_rest_offline_cancels_pending_retry():
    import asyncio
    from unittest.mock import Mock

    async def scenario():
        hass = SimpleNamespace(loop=asyncio.get_running_loop(), async_create_task=asyncio.create_task)
        manager = realtime.MiniBrewRealtimeManager(
            hass, SimpleNamespace(async_update_listeners=Mock()), Mock()
        )
        manager._online_serials = {"serial-1"}
        manager._schedule_reconnect()
        await asyncio.sleep(0)
        retry = manager._reconnect_task
        assert retry is not None
        await manager.async_set_online_devices(set())
        await asyncio.sleep(0)
        assert retry.cancelled()
        assert manager._reconnect_task is None
        manager._client.create_mqtt_client.assert_not_called()

    asyncio.run(scenario())


def test_offline_device_is_unsubscribed_while_another_device_stays_online():
    import asyncio
    from unittest.mock import Mock

    async def scenario():
        async def executor(func, *args):
            return func(*args)

        mqtt = Mock()
        manager = realtime.MiniBrewRealtimeManager(
            SimpleNamespace(async_add_executor_job=executor),
            SimpleNamespace(), None,
        )
        manager._mqtt = mqtt
        manager._subscribed = {"online", "offline"}
        await manager.async_set_online_devices({"online"})
        mqtt.unsubscribe_device_logs.assert_called_once_with("offline")
        mqtt.disconnect.assert_not_called()
        assert manager._subscribed == {"online"}
        assert manager.get_telemetry("offline") is None

    asyncio.run(scenario())

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
