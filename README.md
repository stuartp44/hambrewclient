# MiniBrew Home Assistant Integration

A Home Assistant custom component for integrating MiniBrew Craft and Keg devices into your smart home.

**DISCLAIMER:** This is an unofficial, community-developed integration. It is not affiliated with, endorsed by, officially maintained by, or in any way officially connected to MiniBrew B.V. or any of its subsidiaries or affiliates. The official MiniBrew website can be found at https://minibrew.io. The names "MiniBrew" and related names, marks, emblems and images are registered trademarks of their respective owners.

## Overview

This integration allows you to monitor and control your MiniBrew brewing devices through Home Assistant. It supports both MiniBrew Craft (brewing devices) and MiniBrew Keg (dispensing devices), providing real-time monitoring of temperatures, process phases, and device status.

**IMPORTANT:** A MiniBrew Pro subscription is required for this integration to function. The integration uses the MiniBrew API which requires an active Pro subscription to access device data and control features.

## Features

### Craft Device Sensors
- **Current Temperature** - Real-time temperature monitoring
- **Target Temperature** - Configured target temperature
- **Time in Stage** - Duration in current brewing stage
- **Current Stage** - Active stage information
- **Process Phase** - Current phase details from runtime telemetry
- **Online Status** - Device connectivity status
- **Online Since** - Start of the current observed online period; unavailable while offline (diagnostic)
- **Is Updating** - Firmware update status
- **Needs Cleaning** - Cleaning reminder indicator
- **User Action Required** - Notifications for required user actions
- **Required Action** - Readable prompt such as `Place trub container`
- **Next Action** - Timestamp for the next required user action
- **Session ID** - Active brew session identifier (diagnostic)
- **Brew Session Started** - Session start timestamp from MiniBrew session metadata (diagnostic)
- **ESP Core Temperature** *(real-time MQTT)* - Internal controller temperature

### Keg Device Sensors
- **Current Temperature** - Real-time keg temperature
- **Target Temperature** - Configured serving temperature
- **Beer Name** - Currently loaded beer name (shows `Custom Fermentation` in custom fermentation mode, including inferred fermenting/primary sessions with unknown beer metadata)
- **Beer Style** - Currently loaded beer style (`N/A` in custom fermentation mode)
- **Online Status** - Device connectivity status
- **Online Since** - Start of the current observed online period; unavailable while offline (diagnostic)
- **Is Updating** - Firmware update status
- **Needs Cleaning** - Cleaning reminder indicator
- **Action Required** - Notifications for required user actions
- **Required Action** - Readable prompt such as `Place trub container`
- **Next Action** - Timestamp for the next required user action
- **Session ID** - Active brew session identifier (diagnostic)
- **Brew Session Started** - Session start timestamp from MiniBrew session metadata (diagnostic)
- **Temp Control Power** *(real-time MQTT)* - Peltier heating/cooling power (%)
- **Peltier Mode** *(real-time MQTT)* - Cooling / warming / idle state derived from Peltier power
- **Fan Duty** *(real-time MQTT)* - Peltier fan output (%)
- **ESP Core Temperature** *(real-time MQTT)* - Internal controller temperature
- **Button** *(real-time MQTT)* - Button state

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/stuartp44/hambrewclient`
6. Select category: "Integration"
7. Click "Add"
8. Find "MiniBrew" in the integration list and click "Download"
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/minibrew` directory to your Home Assistant's `custom_components` directory
2. If the `custom_components` directory doesn't exist, create it in the same directory as your `configuration.yaml`
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "MiniBrew"
4. Enter your MiniBrew account credentials:
   - **Username**: Your MiniBrew account username
   - **Password**: Your MiniBrew account password
5. Click **Submit**

### Options

After adding the integration, you can configure additional options:

- **Real-time discovery interval**: How often to refresh discovery and fallback data from REST while MQTT drives live telemetry (default: 300 seconds)
- MQTT-backed sensors report `Unavailable` until fresh telemetry arrives from that device. If telemetry is arriving but an individual field is missing, that sensor reports `Unknown`.
- The integration requests frequent device logs automatically and re-requests them after five seconds without incoming telemetry. Opening the MiniBrew portal or iOS app should not be necessary to start the stream; offline devices still cannot provide live data.
- REST refreshes supplement discovery with device-status fields, including pending user actions and their estimated deadlines. When MQTT reports no action but REST reports a pending action, the REST action is retained. Independently, the live MQTT countdown supplies `Next Action`; a pending REST action does not suppress it. REST provides the timestamp fallback when MQTT omits it.

To access options:
1. Go to **Settings** → **Devices & Services**
2. Find the MiniBrew integration
3. Click **Configure**

### Sensor availability and connectivity

- Device information displays the MiniBrew firmware version without its ESP-IDF build suffix (for example, `3.2.3` instead of `3.2.3, idf-v4.2-50-g11005797d`). The original API value is unchanged.
- **Unavailable** means the sensor's required data source cannot currently provide data. Current temperatures, target temperatures, and action reminders require a positive cloud online status or fresh device telemetry. Firmware update and cleaning status require an online device in a successful cloud response.
- **Unknown** means the required source is reachable but the individual value is absent. Missing action data is not treated as "No action required", and valid zero measurements are not hidden.
- Beer metadata, current stage, and session metadata remain available while a device is offline if a successful cloud response still supplies that device. Missing metadata, such as a session ID when no session is active, reports `Unknown`.
- **Cloud Connection** reports `Offline` when REST explicitly reports the device offline; MQTT cannot override that status. A missing cloud status reports `Unknown` unless fresh permitted telemetry establishes connectivity. A failed cloud refresh makes it `Unavailable`. The **MQTT** diagnostic reports the broker connection separately: a connected broker alone does not prove a Craft or Keg is online.
- REST controls MQTT startup and retries. A device must be explicitly online **and** report a known non-idle `process_type`: brewing, fermentation, serving, or cleaning (including acid cleaning). Online-but-idle devices, missing/unknown process types, and a session ID alone do not enable MQTT. A failed REST overview refresh pauses MQTT too. When no eligible devices remain, the connection is stopped and retries are cancelled. A successful REST refresh reporting an eligible device resumes MQTT; only eligible devices are subscribed. With multiple devices, the shared broker connection can remain active for an eligible device while another device is idle/offline. Process starts and stops are discovered on the configured REST discovery interval (default: 300 seconds). Cloud Connection and Online Since still reflect device connectivity, even while MQTT is intentionally paused for an idle device.
- MQTT-only readings (process phase, Peltier power/mode, fan duty, controller temperature, and button) require device-specific telemetry received within the last **60 seconds** and an active MQTT connection. Cached telemetry is discarded on disconnection and expires after 60 seconds of silence, independently of REST polling.
- **Online Since** stays fixed throughout the current observed online period, becomes `Unavailable` when connectivity is lost, and restarts on reconnection. If Home Assistant starts while a device is already online, this is the time it first observed the device online, not the device's actual connection time. The entity's unique ID is unchanged.
- **Next Action** follows the current reported deadline, rounded to the minute, including overdue deadlines. It does not pin old timestamps across session changes. MQTT action `0` does not clear a pending REST action; a REST refresh clears that prompt. Missing MQTT fields do not overwrite valid cloud data, and telemetry carrying a different session ID cannot restore an old session's reminders. Cleaning/setup actions without a brew session remain supported.
- An explicit REST session value, including `session_id: null`, takes precedence over cached session metadata. A session lookup completing after a reset cannot restore the ended session's per-device start time. Process phase `None` alone is not treated as proof of session completion; the portal's idle response explicitly reports `process_type: 0`, `stage: "Idle"`, and `session_id: null`.

## Requirements

- Home Assistant 2023.1 or newer
- MiniBrew account with registered devices
- **MiniBrew Pro subscription** (required for API access)
- [`pymbrewclient>=1.11.1`](https://github.com/stuartp44/pymbrewclient/releases/tag/v1.11.1) (automatically installed)

## Dependencies

This integration uses the [pymbrewclient](https://github.com/stuartp44/pymbrewclient) library to communicate with the MiniBrew API.

## Usage

Once configured, your MiniBrew devices will appear as separate devices in Home Assistant with all associated sensors. You can:

- View all sensors in the Devices page
- Add sensors to your dashboards
- Create automations based on sensor states
- Monitor brewing progress in real-time

## Troubleshooting

### No devices found
- Verify your MiniBrew account credentials
- Ensure your devices are properly registered in the MiniBrew app
- Check your internet connection

### Connection errors
- Verify Home Assistant can reach the internet
- Check if the MiniBrew service is online
- Review Home Assistant logs for detailed error messages

### Enable debug logging

Add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.minibrew: debug
    pymbrewclient: debug
```

### Action required but Next Action is Unknown

`Next Action` is a timestamp, not the description of the required action.
MiniBrew can report a pending action without providing a REST deadline. The MQTT
countdown is used independently when available, even if its action field is zero
and REST reports a pending instruction. `Next Action` remains a timestamp sensor;
it is unknown only when neither applicable source supplies a usable timestamp.
`Required Action` shows the reconciled prompt (for example, `Place trub container`),
`No action required` when explicitly cleared, or `Unknown` when no action data is
available. Unrecognized action codes are displayed explicitly rather than guessed.

The action sensors and `Next Action` expose diagnostic attributes:
`rest_user_action`, `mqtt_user_action`, `rest_next_action_at`,
`mqtt_next_action_at`, `mqtt_seconds_until_next_action`,
`mqtt_session_matches`, `action_conflict`, and `deadline_status`.
`deadline_status: deadline_not_provided` explains why a pending action has an
unknown timestamp. Raw MQTT values remain visible for diagnosis even if excluded
from reconciliation; a different session is flagged and is not compared as an
action conflict. Failed/offline REST data is not presented as current.
These attributes explain source discrepancies; they do not change the MQTT
process phase or infer a deadline from the process state.
The same sensors also expose `required_action` and `action_status` (for example,
`Place trub container` and `Waiting for user action`). `waiting_since` is the
REST-reported process-state entry time, included only while the pending action
matches the current REST action. It is not a due date, is cleared when the action
clears or changes, and remains empty when no trustworthy time is available.

The [anonymized live-capture excerpt](tests/fixtures/pending_action_mqtt.json)
demonstrates this case:

- REST reports action `30` (place trub container), process state `78`, and no deadline.
- MQTT reports the same session and process state, but action `0` (undefined).
- Two MQTT messages 29.020 seconds apart both report `604800` seconds (seven days)
  until the next action. The calculated timestamp advances rather than counting down.
- The integration retains the REST action and uses the MQTT timestamp for
  `Next Action`. The current instruction and next-action estimate are independent;
  the estimate is not rejected merely because the countdown remains constant
  while the device waits.

The excerpt contains only selected decoded fields from the first and last messages
of a 30-message capture. Device and session identifiers and absolute timestamps are
replaced with example values; relative timing and relevant protocol values are
preserved. Raw protobuf payloads and unrelated metadata are deliberately excluded
to avoid publishing identifying or unknown embedded fields. This sample documents
observed behavior, not a claim that all MQTT countdowns behave this way.

## Contributing

Contributions are welcome! This project uses **Conventional Commits** and automated semantic versioning.

### Commit Message Format

All commits **MUST** follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>
```

**Types:**
- `feat`: New feature (triggers minor version bump)
- `fix`: Bug fix (triggers patch version bump)
- `docs`: Documentation changes
- `style`: Code style changes
- `refactor`: Code refactoring
- `perf`: Performance improvements (triggers patch version bump)
- `test`: Test changes
- `build`: Build system changes (triggers patch version bump)
- `ci`: CI/CD changes (triggers patch version bump)
- `chore`: Other changes (triggers patch version bump)

**Examples:**
```bash
feat(sensor): add battery level sensor
fix(config-flow): correct validation error
docs: update installation instructions
```

### Automated Releases

- Versions are automatically generated from commit messages
- Releases are created automatically on merge to `main`
- Changelog is auto-generated from commits
- Tags follow semantic versioning (v1.0.0, v1.1.0, etc.)
- Patch releases are triggered by `fix`, `perf`, `ci`, `build`, and `chore` commits

**Please read [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.**

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or feature requests, please open an issue on [GitHub](https://github.com/stuartp44/hambrewclient/issues).

## Disclaimer

This integration is an independent, community-driven project developed and maintained by volunteers. It is not affiliated with, endorsed by, officially maintained by, or in any way officially connected to MiniBrew B.V. or any of its subsidiaries or affiliates.

- This software is provided "as is" without warranty of any kind
- Use at your own risk
- The developers are not responsible for any damage to your MiniBrew devices
- MiniBrew® is a registered trademark of MiniBrew B.V.
- For official MiniBrew support, please visit https://minibrew.io

## Credits

- Developed by [@stuartp44](https://github.com/stuartp44)
- Built for the Home Assistant community
- Development infrastructure and automation assisted by GitHub Copilot
