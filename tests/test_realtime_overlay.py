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
        "current_temperature": None,
        "target_temperature": None,
        "user_action": None,
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


def test_user_action_zero_is_applied_not_skipped():
    # 0 is falsy but not None, so it must still overlay.
    device = {"user_action": 5}
    realtime.overlay_mqtt(device, _telemetry(user_action=0))
    assert device["user_action"] == 0


def test_seconds_until_next_action_maps_to_remaining_seconds():
    device = {"process_estimate_remaining_seconds": 1200}
    realtime.overlay_mqtt(device, _telemetry(seconds_until_next_action=420))
    assert device["process_estimate_remaining_seconds"] == 420


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
