# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Require `pymbrewclient>=1.11.0` to align with SensorType telemetry support

### Fixed
- Handle Device objects returned by the API without setup errors

### Added
- Map real-time `seconds_until_next_action` to `process_estimate_remaining_seconds`
- Add real-time diagnostic sensors for Peltier fan power and ESP core temperature

### Added
- Initial release of MiniBrew Home Assistant integration
- Support for MiniBrew Craft devices
- Support for MiniBrew Keg devices
- Temperature monitoring sensors
- Brew stage tracking
- Device status monitoring
- Configurable refresh interval
- Automated release pipeline with semantic versioning
- Conventional commits enforcement

[Unreleased]: https://github.com/stuartp44/hambrewclient/commits/main
