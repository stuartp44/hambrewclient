"""Real-time MQTT telemetry manager for the MiniBrew integration.

Wraps ``pymbrewclient``'s MQTT-over-WebSocket client so the sensor coordinator
can overlay live device telemetry on top of the slower REST poll.

The underlying ``MqttClient`` runs paho with its own network thread, so its
callbacks fire *off* the Home Assistant event loop. Every interaction with HA
(entity state writes via the coordinator listeners) is therefore marshalled
back onto the loop with ``hass.loop.call_soon_threadsafe``.
"""

import logging
import threading

_LOGGER = logging.getLogger(__name__)

# Maps ``DeviceLogMessage`` attribute -> the REST device-dict key the existing
# sensors already read. Keeping it here (with no Home Assistant imports) lets the
# mapping be unit-tested without a running HA instance.
_TELEMETRY_TO_DEVICE_KEY = {
    "current_temperature": "current_temp",
    "target_temperature": "target_temp",
    "user_action": "user_action",
    "next_action_at": "process_estimate_remaining",
    "seconds_until_next_action": "process_estimate_remaining_seconds",
}


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
        self._telemetry_lock = threading.Lock()
        self._subscribed = set()
        self._connected = False

    async def async_start(self):
        """Create and connect the MQTT client (runs blocking I/O in executor)."""
        try:
            self._mqtt = await self.hass.async_add_executor_job(self._client.create_mqtt_client)
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not create MQTT client: %s", err)
            self._mqtt = None
            return

        self._mqtt.on_device_log(self._handle_device_log)
        self._mqtt.on_connected(self._handle_connected)
        self._mqtt.on_disconnected(self._handle_disconnected)
        self._mqtt.on_error(self._handle_error)

        try:
            await self.hass.async_add_executor_job(self._mqtt.connect)
        except Exception as err:  # noqa: BLE001 - never break REST polling
            _LOGGER.warning("MiniBrew realtime: could not connect to MQTT broker: %s", err)

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

    @property
    def connected(self):
        """Return whether the MQTT stream is currently connected."""
        return self._connected

    async def async_stop(self):
        """Disconnect the MQTT client and stop its network thread."""
        if self._mqtt is None:
            return
        mqtt, self._mqtt = self._mqtt, None
        try:
            await self.hass.async_add_executor_job(mqtt.disconnect)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MiniBrew realtime: error during disconnect: %s", err)
        self._connected = False

    # ------------------------------------------------------------------
    # paho-thread callbacks — marshal HA work back onto the event loop
    # ------------------------------------------------------------------

    def _handle_device_log(self, msg):
        """Store telemetry and notify coordinator listeners (paho thread)."""
        serial = msg.device_uuid
        if not serial:
            return
        with self._telemetry_lock:
            self._telemetry[serial] = msg
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)

    def _handle_connected(self):
        """Mark connected and refresh entity availability (paho thread)."""
        self._connected = True
        _LOGGER.debug("MiniBrew realtime: MQTT connected")
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)

    def _handle_disconnected(self):
        """Mark disconnected and refresh entity availability (paho thread)."""
        self._connected = False
        _LOGGER.debug("MiniBrew realtime: MQTT disconnected")
        self.hass.loop.call_soon_threadsafe(self.coordinator.async_update_listeners)

    def _handle_error(self, exc):
        """Log connection-level MQTT errors (paho thread). Token is never included."""
        _LOGGER.warning("MiniBrew realtime: MQTT error: %s", exc)
