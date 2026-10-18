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
from datetime import datetime, timedelta, timezone

_LOGGER = logging.getLogger(__name__)

# Seconds to wait before attempting to reconnect after a disconnect or initial
# connection failure.
_RECONNECT_DELAY = 30

# Device logs are requested again after five seconds of silence.
_TELEMETRY_MAX_AGE = timedelta(seconds=60)

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

# Process-state/action pairs defined by the client protocol enums. State 78
# reporting action 0 was also verified in the anonymized live capture.
_PROCESS_STATE_ACTIONS = {
    75: 26,  # Prepare fermentation.
    76: 28,  # Place airlock.
    77: 29,  # Remove airlock.
    78: 30,  # Place trub container.
    81: 8,   # Remove trub.
    82: 4,   # Add ingredient.
    83: 5,   # Remove ingredient.
    85: 36,  # Pair gravity sensor.
    91: 10,  # Mount tap.
}
_AUTOMATIC_PROCESS_STATES = {0, 80, 90, 92, 94}

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


def overlay_mqtt(device: dict, telemetry, *, include_session=True) -> None:
    """Overlay live MQTT telemetry onto a REST device dict, in place.

    Only non-``None`` telemetry values win, so a missing field never clobbers
    the value from the last REST poll. MQTT action/process context takes priority
    over REST; undefined action codes without recognized context fall back to REST.
    The MQTT countdown is independent of the current user prompt.
    """
    if telemetry is None:
        return
    action = getattr(telemetry, "user_action", None)
    process_state = getattr(telemetry, "process_state", None)
    device["action_source"] = "rest" if device.get("user_action") is not None else None
    if include_session:
        if action is not None and action > 0:
            device["user_action"] = action
            device["action_source"] = "mqtt"
        elif process_state in _PROCESS_STATE_ACTIONS:
            device["user_action"] = _PROCESS_STATE_ACTIONS[process_state]
            device["action_source"] = "mqtt_process_state"
        elif process_state in _AUTOMATIC_PROCESS_STATES:
            device["user_action"] = 0
            device["action_source"] = "mqtt_process_state"
        elif action == 0 and device.get("user_action") is None:
            device["user_action"] = 0
            device["action_source"] = "mqtt"
    for attr, device_key in _TELEMETRY_TO_DEVICE_KEY.items():
        if not include_session and attr not in {"current_temperature"}:
            continue
        if attr == "user_action":
            continue
        value = getattr(telemetry, attr, None)
        if value is not None:
            device[device_key] = value
            if device_key in {"current_temp", "target_temp", "process_estimate_remaining"}:
                device.setdefault("_field_sources", {})[device_key] = "mqtt"


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
        self._last_received = {}
        self._expiry_handles = {}
        self._telemetry_lock = threading.Lock()
        self._subscribed = set()
        self._online_serials = set()
        self._connected = False
        self._reconnect_task = None
        self._stopped = False

    async def async_start(self, online_serials):
        """Create and connect the MQTT client (initial entry point from HA setup)."""
        self._stopped = False
        self._online_serials = set(online_serials)
        await self._async_do_connect()

    async def async_set_online_devices(self, online_serials):
        """Reconcile devices REST confirms are online with an active process."""
        self._online_serials = set(online_serials)
        if not self._online_serials:
            if not self._stopped:
                _LOGGER.info("MiniBrew realtime: pausing MQTT until REST reports an online device with an active process")
                await self.async_stop()
            return
        with self._telemetry_lock:
            for cache in (
                self._telemetry, self._fingerprints, self._last_received, self._last_update
            ):
                for serial in set(cache) - self._online_serials:
                    cache.pop(serial, None)
        for serial in set(self._expiry_handles) - self._online_serials:
            self._expiry_handles.pop(serial).cancel()
        for serial in self._subscribed - self._online_serials:
            if self._mqtt is not None:
                await self.hass.async_add_executor_job(self._mqtt.unsubscribe_device_logs, serial)
            self._subscribed.discard(serial)
        self._stopped = False
        retry_pending = self._reconnect_task is not None and not self._reconnect_task.done()
        if self._mqtt is None and not retry_pending:
            await self._async_do_connect()
        self.async_ensure_subscribed(self._online_serials)

    def _dispose_mqtt_client(self, mqtt=None):
        """Disconnect an MQTT client and drop our reference to it.

        This avoids leaking stale paho sockets and threads when the broker is
        unavailable or a connection attempt fails and we immediately retry.
        """
        mqtt = self._mqtt if mqtt is None else mqtt
        if mqtt is None:
            return
        try:
            mqtt.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MiniBrew realtime: error during disconnect: %s", err)
        if self._mqtt is mqtt:
            self._mqtt = None

    async def _async_do_connect(self):
        """Internal: create the MQTT client and connect. Called from async_start and reconnect."""
        if self._stopped or not self._online_serials:
            return
        if self._mqtt is not None:
            self._dispose_mqtt_client()
        try:
            self._mqtt = await self.hass.async_add_executor_job(self._client.create_mqtt_client)
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not create MQTT client: %s", err)
            self._mqtt = None
            self._schedule_reconnect()
            return

        if self._stopped or not self._online_serials:
            await self.hass.async_add_executor_job(self._dispose_mqtt_client)
            return

        self._mqtt.on_device_log(self._handle_device_log)
        self._mqtt.on_connected(self._handle_connected)
        self._mqtt.on_disconnected(self._handle_disconnected)
        self._mqtt.on_error(self._handle_error)

        _LOGGER.info("MiniBrew realtime: connecting to MQTT broker…")
        try:
            await self.hass.async_add_executor_job(self._mqtt.connect)
            _LOGGER.info("MiniBrew realtime: MQTT connect() returned (waiting for on_connected callback)")
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not connect to MQTT broker: %s", err)
            self._dispose_mqtt_client(self._mqtt)
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
            if serial not in self._online_serials or serial in self._subscribed:
                continue
            try:
                self._mqtt.subscribe_device_logs(serial)
                self._subscribed.add(serial)
                _LOGGER.debug("MiniBrew realtime: subscribed to logs for %s", serial)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("MiniBrew realtime: subscribe failed for %s: %s", serial, err)

    def get_telemetry(self, serial):
        """Return telemetry only while connected and receiving device logs."""
        if serial not in self._online_serials:
            return None
        with self._telemetry_lock:
            received = self._last_received.get(serial)
            if not self._connected or received is None:
                return None
            if datetime.now(timezone.utc) - received >= _TELEMETRY_MAX_AGE:
                return None
            return self._telemetry.get(serial)

    def _async_schedule_expiry(self, serial):
        """Ensure silence triggers a state update even without a REST poll."""
        handle = self._expiry_handles.pop(serial, None)
        if handle is not None:
            handle.cancel()
        if self._stopped or serial not in self._online_serials:
            return
        self._expiry_handles[serial] = self.hass.loop.call_later(
            _TELEMETRY_MAX_AGE.total_seconds(), self._async_expire_telemetry, serial
        )

    def _async_expire_telemetry(self, serial):
        self._expiry_handles.pop(serial, None)
        with self._telemetry_lock:
            received = self._last_received.get(serial)
            if received is not None:
                remaining = (
                    _TELEMETRY_MAX_AGE - (datetime.now(timezone.utc) - received)
                ).total_seconds()
                if remaining > 0:
                    self._expiry_handles[serial] = self.hass.loop.call_later(
                        remaining, self._async_expire_telemetry, serial
                    )
                    return
            self._telemetry.pop(serial, None)
            self._fingerprints.pop(serial, None)
            self._last_received.pop(serial, None)
            self._last_update.pop(serial, None)
        self.coordinator.async_update_listeners()

    def _clear_telemetry(self):
        with self._telemetry_lock:
            self._telemetry.clear()
            self._fingerprints.clear()
            self._last_received.clear()
            self._last_update.clear()

        def cancel_expiry():
            for handle in self._expiry_handles.values():
                handle.cancel()
            self._expiry_handles.clear()

        self.hass.loop.call_soon_threadsafe(cancel_expiry)

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
        self._clear_telemetry()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._mqtt is not None:
            mqtt = self._mqtt
            self._mqtt = None
            try:
                await self.hass.async_add_executor_job(mqtt.disconnect)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("MiniBrew realtime: error during disconnect: %s", err)
        self._connected = False
        self._subscribed.clear()

    def _schedule_reconnect(self):
        """Schedule a reconnect attempt on the HA event loop (safe to call from any thread)."""
        if self._stopped or not self._online_serials:
            return

        def _do_schedule():
            if self._stopped or not self._online_serials:
                return
            if self._reconnect_task is not None and not self._reconnect_task.done():
                return
            _LOGGER.warning(
                "MiniBrew realtime: scheduling reconnect in %s seconds", _RECONNECT_DELAY
            )
            self._reconnect_task = self.hass.async_create_task(self._async_reconnect())

        self.hass.loop.call_soon_threadsafe(_do_schedule)

    async def _async_reconnect(self):
        """Wait, then attempt to reconnect the MQTT stream."""
        await asyncio.sleep(_RECONNECT_DELAY)
        if self._stopped or not self._online_serials:
            return
        _LOGGER.warning("MiniBrew realtime: attempting to reconnect…")
        # Reset subscribed set — _handle_connected will re-subscribe once the connection is up.
        self._subscribed.clear()
        await self._async_do_connect()

    # ------------------------------------------------------------------
    # paho-thread callbacks — marshal HA work back onto the event loop
    # ------------------------------------------------------------------

    def _handle_device_log(self, msg):
        """Store telemetry and notify listeners only on meaningful changes."""
        serial = msg.device_uuid
        if self._stopped or serial not in self._online_serials:
            return
        if getattr(msg, "decode_error", None):
            _LOGGER.warning("MiniBrew realtime: invalid telemetry from %s: %s", serial, msg.decode_error)
            return

        fingerprint = _telemetry_fingerprint(msg)
        with self._telemetry_lock:
            now = datetime.now(timezone.utc)
            previous_received = self._last_received.get(serial)
            changed = (
                self._fingerprints.get(serial) != fingerprint
                or previous_received is None
                or now - previous_received >= _TELEMETRY_MAX_AGE
            )
            self._telemetry[serial] = msg
            self._fingerprints[serial] = fingerprint
            self._last_received[serial] = now
            if changed:
                self._last_update[serial] = getattr(msg, "received_at", None)

        self.hass.loop.call_soon_threadsafe(lambda: self._async_schedule_expiry(serial))
        if not changed:
            return

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
        """Mark connected, subscribe to all known serials, and refresh entity availability (paho thread)."""
        if self._stopped or not self._online_serials:
            return
        self._connected = True
        _LOGGER.info("MiniBrew realtime: MQTT connected")
        # Subscribe here — this is the earliest safe point after the WS handshake completes.
        # Both initial connection and reconnects land here, so subscriptions are always set up.
        def _subscribe_and_notify():
            self.async_ensure_subscribed(self._online_serials)
            self.coordinator.async_update_listeners()
        self.hass.loop.call_soon_threadsafe(_subscribe_and_notify)

    def _handle_disconnected(self):
        """Mark disconnected, refresh entity availability, and schedule reconnect (paho thread)."""
        self._connected = False
        self._clear_telemetry()
        if self._stopped or not self._online_serials:
            self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)
            return
        _LOGGER.warning("MiniBrew realtime: MQTT disconnected — will retry in %s s", _RECONNECT_DELAY)
        self._dispose_mqtt_client()
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)
        self._schedule_reconnect()

    def _handle_error(self, exc):
        """Log connection-level MQTT errors (paho thread). Token is never included."""
        _LOGGER.warning("MiniBrew realtime: MQTT error: %s", exc)
