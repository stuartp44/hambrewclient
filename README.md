# MiniBrew Home Assistant Integration

A Home Assistant custom component for integrating MiniBrew Craft and Keg devices into your smart home.

**DISCLAIMER:** This is an unofficial, community-developed integration. It is not affiliated with, endorsed by, officially maintained by, or in any way officially connected to MiniBrew B.V. or any of its subsidiaries or affiliates. The official MiniBrew website can be found at https://minibrew.io. The names "MiniBrew" and related names, marks, emblems and images are registered trademarks of their respective owners.

## Overview

This integration allows you to monitor and control your MiniBrew brewing devices through Home Assistant. It supports both MiniBrew Craft (brewing devices) and MiniBrew Keg (dispensing devices), providing real-time monitoring of temperatures, brew stages, and device status.

**IMPORTANT:** A MiniBrew Pro subscription is required for this integration to function. The integration uses the MiniBrew API which requires an active Pro subscription to access device data and control features.

## Features

### Craft Device Sensors
- **Current Temperature** - Real-time temperature monitoring
- **Target Temperature** - Configured target temperature
- **Brew Stage** - Current brewing stage
- **Time in Stage** - Duration in current brewing stage
- **Current Stage** - Active stage information
- **Online Status** - Device connectivity status
- **Is Updating** - Firmware update status
- **Needs Cleaning** - Cleaning reminder indicator
- **User Action Required** - Notifications for required user actions

### Keg Device Sensors
- **Current Temperature** - Real-time keg temperature
- **Target Temperature** - Configured serving temperature
- **Beer Style** - Currently loaded beer style
- **Online Status** - Device connectivity status
- **Is Updating** - Firmware update status
- **Needs Cleaning** - Cleaning reminder indicator
- **Action Required** - Notifications for required user actions

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

1. Copy the `custom_components/hahbrewclient` directory to your Home Assistant's `custom_components` directory
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

- **Refresh Interval**: How often to poll the MiniBrew API for updates (default: 60 seconds)

To access options:
1. Go to **Settings** → **Devices & Services**
2. Find the MiniBrew integration
3. Click **Configure**

## Requirements

- Home Assistant 2023.1 or newer
- MiniBrew account with registered devices
- **MiniBrew Pro subscription** (required for API access)
- `pymbrewclient>=1.0.10` (automatically installed)

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
- `build`: Build system changes
- `ci`: CI/CD changes
- `chore`: Other changes

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
