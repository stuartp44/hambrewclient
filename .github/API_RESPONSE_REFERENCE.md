# MiniBrew API Response Reference

This document shows the structure of the MiniBrew API response for brewery overview.

## Response Structure

The API returns devices grouped by their current state:

- `brew_clean_idle` - Craft devices in idle or clean state
- `fermenting` - Devices currently fermenting
- `serving` - Keg devices serving beer
- `brew_acid_clean_idle` - Craft devices in acid clean idle state

## Example Response

```json
{
    "brew_clean_idle": [
        {
            "uuid": "2301B0123-XXXXXXXX",
            "serial_number": "2301B0123-XXXXXXXX",
            "device_type": 0,
            "user_action": 0,
            "process_type": 0,
            "title": "My MiniBrew Craft",
            "sub_title": "Connected",
            "session_id": null,
            "image": "https://minibrew.s3.amazonaws.com/static/devices/base.png",
            "status_time": null,
            "stage": "Idle",
            "beer_name": null,
            "recipe_version": null,
            "beer_style": null,
            "gravity": "1.00",
            "target_temp": null,
            "current_temp": null,
            "online": true,
            "updating": false,
            "needs_acid_cleaning": false,
            "is_starting": null,
            "software_version": "3.2.3, idf-v4.2-50-g11005797d"
        }
    ],
    "fermenting": [
        {
            "uuid": "2301K0456-YYYYYYYY",
            "serial_number": "2301K0456-YYYYYYYY",
            "device_type": 1,
            "user_action": 0,
            "process_type": 4,
            "title": "Keg 2301K0456",
            "sub_title": "Connected",
            "session_id": 12345,
            "image": "https://minibrew.s3.amazonaws.com/static/devices/keg.png",
            "status_time": 172800,
            "stage": "Primary",
            "beer_name": "My IPA",
            "recipe_version": "1",
            "beer_style": "American IPA",
            "gravity": "1.00",
            "target_temp": 18.0,
            "current_temp": 18.2,
            "online": true,
            "updating": false,
            "needs_acid_cleaning": false,
            "is_starting": false,
            "software_version": "3.2.3, idf-v4.2-50-g11005797d"
        }
    ],
    "serving": [],
    "brew_acid_clean_idle": []
}
```

## Device Fields

### Identification
- `uuid` - Unique device identifier
- `serial_number` - Device serial number
- `device_type` - Device type (0 = Craft, 1 = Keg)
- `title` - User-friendly device name
- `sub_title` - Connection status description

### Status
- `online` - Boolean, device connectivity status
- `updating` - Boolean, firmware update in progress
- `stage` - Current stage (e.g., "Idle", "Primary", "Mashing", etc.)
- `status_time` - Time in current stage (seconds)
- `user_action` - User action required code
- `process_type` - Current process type code

### Brewing/Fermentation
- `session_id` - Active brewing/fermentation session ID (null if idle)
- `beer_name` - Name of current beer
- `recipe_version` - Recipe version number
- `beer_style` - Beer style (e.g., "German Pils")
- `gravity` - Specific gravity reading
- `target_temp` - Target temperature (°C)
- `current_temp` - Current temperature (°C)

### Maintenance
- `needs_acid_cleaning` - Boolean, acid cleaning required
- `is_starting` - Boolean, device starting up

### System
- `software_version` - Device firmware version
- `image` - URL to device image

## Device Types

| Value | Type | Description |
|-------|------|-------------|
| 0 | Craft | MiniBrew Craft brewing device |
| 1 | Keg | MiniBrew Keg dispensing device |

## Process States

Devices are grouped into arrays based on their current process state:

| State | Description |
|-------|-------------|
| `brew_clean_idle` | Craft devices that are idle or in clean mode |
| `fermenting` | Devices actively fermenting beer |
| `serving` | Keg devices currently serving beer |
| `brew_acid_clean_idle` | Craft devices in acid clean idle state |

## Integration Usage

The integration uses this API response to:
1. Discover all devices associated with the account
2. Create sensor entities for each device
3. Update sensor states based on device properties
4. Group devices by type (Craft vs Keg)
5. Monitor brewing/fermentation progress
6. Track maintenance requirements

## Notes

- Temperature values are in Celsius
- Gravity is specific gravity (1.000 = water)
- `status_time` is in seconds and represents time in current stage
- `null` values indicate the field is not applicable for the current device state
- A MiniBrew Pro subscription is required to access this API
