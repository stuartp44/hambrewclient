"""Real-time MQTT telemetry manager for the MiniBrew integration.

Wraps ``pymbrewclient``'s MQTT-over-WebSocket client so the sensor coordinator
can overlay live device telemetry on top of the slower REST poll.

The underlying ``MqttClient`` runs paho with its own network thread, so its
callbacks fire *off* the Home Assistant event loop. Every interaction with HA
(entity state writes via the coordinator listeners) is therefore marshalled
back onto the loop with ``hass.loop.call_soon_threadsafe``.
"""

import asyncio
import logging
import threading
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# Seconds to wait before attempting to reconnect after a disconnect or initial
# connection failure.
_RECONNECT_DELAY = 30

# Maps ``DeviceLogMessage`` attribute -> the REST device-dict key the existing
# sensors already read. Keeping it here (with no Home Assistant imports) lets the
# mapping be unit-tested without a running HA instance.
_TELEMETRY_TO_DEVICE_KEY = {
    "current_state": "current_state",
    "process_type": "process_type",
    "process_state": "process_state",
    "current_temperature": "current_temp",
    "target_temperature": "target_temp",
    "user_action": "user_action",
    "process_phase": "process_phase",
    "session_id": "active_session",
    "next_action_at": "process_estimate_remaining",
    "seconds_until_next_action": "process_estimate_remaining_seconds",
}

# Fingerprinting drives listener notifications. Exclude countdown fields that
# can tick every second without changing user-visible state.
_FINGERPRINT_ATTRS = tuple(
    attr for attr in _TELEMETRY_TO_DEVICE_KEY if attr != "seconds_until_next_action"
)

def _normalize_fingerprint_value(value, *, trim_to_minute=False):
    """Normalize values for stable change detection."""
    if isinstance(value, datetime):
        if trim_to_minute:
            return value.replace(second=0, microsecond=0)
        return value.replace(microsecond=0)
    return value


def _telemetry_fingerprint(msg):
    """Return the effective telemetry state used by entities."""
    attrs = {}
    for attr in _FINGERPRINT_ATTRS:
        attrs[attr] = _normalize_fingerprint_value(
            getattr(msg, attr, None),
            trim_to_minute=(attr == "next_action_at"),
        )
    attrs["temp_control_power"] = _normalize_fingerprint_value(
        getattr(msg, "temp_control_power", None)
    )
    measurements = getattr(msg, "measurements", None)
    if isinstance(measurements, dict):
        attrs["measurements"] = tuple(
            sorted((str(key), _normalize_fingerprint_value(val)) for key, val in measurements.items())
        )
    else:
        attrs["measurements"] = None
    return tuple(sorted(attrs.items()))


def overlay_mqtt(device: dict, telemetry) -> None:
    """Overlay live MQTT telemetry onto a REST device dict, in place.

    Only non-``None`` telemetry values win, so a missing field never clobbers
    the value from the last REST poll.
    """
    if telemetry is None:
        return
    for attr, device_key in _TELEMETRY_TO_DEVICE_KEY.items():
        value = getattr(telemetry, attr, None)
        if value is not None:
            if attr == "user_action":
                existing = device.get(device_key)
                # Some MQTT payloads report 0 when action context is absent.
                # Preserve a non-zero REST action in that case.
                if value == 0 and isinstance(existing, (int, float)) and existing > 0:
                    continue
            device[device_key] = value


class MiniBrewRealtimeManager:
    """Owns the MQTT client lifecycle and a per-serial telemetry store."""

    def __init__(self, hass, coordinator, client):
        """Initialize the manager.

        :param hass: The Home Assistant instance.
        :param coordinator: The MiniBrew ``DataUpdateCoordinator`` whose
            listeners are notified when new telemetry arrives.
        :param client: The ``BreweryClient`` used to mint an MQTT client.
        """
        self.hass = hass
        self.coordinator = coordinator
        self._client = client
        self._mqtt = None
        self._telemetry = {}
        self._fingerprints = {}
        self._last_update = {}
        self._telemetry_lock = threading.Lock()
        self._subscribed = set()
        self._connected = False
        self._reconnect_task = None
        self._stopped = False

    async def async_start(self):
        """Create and connect the MQTT client (runs blocking I/O in executor)."""
        self._stopped = False
        try:
            self._mqtt = await self.hass.async_add_executor_job(self._client.create_mqtt_client)
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not create MQTT client: %s", err)
            self._mqtt = None
            self._schedule_reconnect()
            return

        self._mqtt.on_device_log(self._handle_device_log)
        self._mqtt.on_connected(self._handle_connected)
        self._mqtt.on_disconnected(self._handle_disconnected)
        self._mqtt.on_error(self._handle_error)

        try:
            await self.hass.async_add_executor_job(self._mqtt.connect)
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not connect to MQTT broker: %s", err)
            self._schedule_reconnect()

    def async_ensure_subscribed(self, serials):
        """Subscribe to device-log topics for any not-yet-subscribed serials.

        Safe to call from the event loop. ``subscribe_device_logs`` remembers
        topics and auto-resubscribes on reconnect, so this may run before the
        connection is established.
        """
        if self._mqtt is None:
            return
        for serial in serials:
            if not serial or serial in self._subscribed:
                continue
            try:
                self._mqtt.subscribe_device_logs(serial)
                self._subscribed.add(serial)
                _LOGGER.debug("MiniBrew realtime: subscribed to logs for %s", serial)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("MiniBrew realtime: subscribe failed for %s: %s", serial, err)

    def get_telemetry(self, serial):
        """Return the latest decoded telemetry for a serial, or ``None``."""
        with self._telemetry_lock:
            return self._telemetry.get(serial)

    def get_last_update(self, serial):
        """Return timestamp of last meaningful telemetry update for a serial."""
        with self._telemetry_lock:
            return self._last_update.get(serial)

    @property
    def connected(self):
        """Return whether the MQTT stream is currently connected."""
        return self._connected

    async def async_stop(self):
        """Disconnect the MQTT client and stop its network thread."""
        self._stopped = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._mqtt is None:
            return
        mqtt, self._mqtt = self._mqtt, None
        try:
            await self.hass.async_add_executor_job(mqtt.disconnect)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MiniBrew realtime: error during disconnect: %s", err)
        self._connected = False

    def _schedule_reconnect(self):
        """Schedule a reconnect attempt on the HA event loop (safe to call from any thread)."""
        if self._stopped:
            return

        def _do_schedule():
            if self._stopped:
                return
            if self._reconnect_task is not None and not self._reconnect_task.done():
                return
            _LOGGER.info(
                "MiniBrew realtime: scheduling reconnect in %s seconds", _RECONNECT_DELAY
            )
            self._reconnect_task = self.hass.async_create_task(self._async_reconnect())

        self.hass.loop.call_soon_threadsafe(_do_schedule)

    async def _async_reconnect(self):
        """Wait, then attempt to reconnect the MQTT stream."""
        await asyncio.sleep(_RECONNECT_DELAY)
        if self._stopped:
            return
        _LOGGER.info("MiniBrew realtime: attempting to reconnect…")
        # Reset subscribed set so topics are re-subscribed after reconnect.
        self._subscribed.clear()
        await self.async_start()
        # Re-subscribe to all known serials (coordinator data may have serials already).
        from .sensor import _collect_serials  # local import to avoid circular dep at module level
        if self.coordinator.data is not None:
            self.async_ensure_subscribed(_collect_serials(self.coordinator))

    # ------------------------------------------------------------------
    # paho-thread callbacks — marshal HA work back onto the event loop
    # ------------------------------------------------------------------

    def _handle_device_log(self, msg):
        """Store telemetry and notify listeners only on meaningful changes."""
        serial = msg.device_uuid
        if not serial:
            return

        fingerprint = _telemetry_fingerprint(msg)
        with self._telemetry_lock:
            if self._fingerprints.get(serial) == fingerprint:
                return
            self._telemetry[serial] = msg
            self._fingerprints[serial] = fingerprint
            self._last_update[serial] = getattr(msg, "received_at", None)

        _LOGGER.debug(
            "MiniBrew realtime: message from %s (session=%s phase=%s target=%s current=%s action=%s)",
            serial,
            getattr(msg, "session_id", None),
            getattr(msg, "process_phase", None),
            getattr(msg, "target_temperature", None),
            getattr(msg, "current_temperature", None),
            getattr(msg, "user_action", None),
        )
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)

    def _handle_connected(self):
        """Mark connected and refresh entity availability (paho thread)."""
        self._connected = True
        _LOGGER.debug("MiniBrew realtime: MQTT connected")
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)

    def _handle_disconnected(self):
        """Mark disconnected, refresh entity availability, and schedule reconnect (paho thread)."""
        self._connected = False
        _LOGGER.warning("MiniBrew realtime: MQTT disconnected — will retry in %s s", _RECONNECT_DELAY)
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)
        self._schedule_reconnect()

    def _handle_error(self, exc):
        """Log connection-level MQTT errors (paho thread). Token is never included."""
        _LOGGER.warning("MiniBrew realtime: MQTT error: %s", exc)
