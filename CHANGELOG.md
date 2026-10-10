# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.1](https://github.com/stuartp44/hambrewclient/compare/v0.11.0...v0.11.1) (2026-10-10)


### Fixed
- Avoid recursive device-data copying and unnecessary conversions during sensor state reads and MQTT-driven discovery updates ([#37](https://github.com/stuartp44/hambrewclient/issues/37))

* **release:** publish MQTT log recovery in 0.11.1 ([3412332](https://github.com/stuartp44/hambrewclient/commit/341233262c518d2b1c0253203f7b2d7d7df1c8fb))
* **release:** publish MQTT log recovery in 0.11.1 ([646897e](https://github.com/stuartp44/hambrewclient/commit/646897ebbd6ec2767b1c1d4d4c0b4dc94250230b))

## [Unreleased]

### Changed
- Require `pymbrewclient>=1.11.1` for SensorType telemetry support and automatic MQTT log requests and recovery

### Fixed
- Request frequent MQTT logs and recover stalled telemetry through `pymbrewclient>=1.11.1`, without relying on an open MiniBrew portal or iOS app ([#28](https://github.com/stuartp44/hambrewclient/issues/28))
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
