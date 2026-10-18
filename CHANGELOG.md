# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.3](https://github.com/stuartp44/hambrewclient/compare/v0.11.2...v0.11.3) (2026-10-18)


### Fixed

* **realtime:** gate mqtt connections on rest device status ([e5294d4](https://github.com/stuartp44/hambrewclient/commit/e5294d4a9478d48377acd6818a73101f6ffa7f7f))
* **realtime:** require an active process before connecting mqtt ([eebc83e](https://github.com/stuartp44/hambrewclient/commit/eebc83e770a6526b6fb46250a0e5f52e7f7eaae1))
* **sensor:** add required action sensor and enhance action diagnostics ([1c2b700](https://github.com/stuartp44/hambrewclient/commit/1c2b700e8a6e862c33b4742710fd1b889aac11a0))
* **sensor:** apply source-aware availability and clear stale state ([1a73fd9](https://github.com/stuartp44/hambrewclient/commit/1a73fd96ed928a32998a6be24f49db494ecb2bb1))
* **sensor:** apply source-aware availability and clear stale state ([5fb7a66](https://github.com/stuartp44/hambrewclient/commit/5fb7a6663192b88ce2cb95690fa6282c75684745))
* **sensor:** clarify handling of REST actions and MQTT countdowns in documentation and tests ([6d21cc0](https://github.com/stuartp44/hambrewclient/commit/6d21cc027a0da4a9a44442a04ae2d27db358d5bf))
* **sensor:** derive current stage from reported process type ([fcbbadd](https://github.com/stuartp44/hambrewclient/commit/fcbbadd6ac67f40a0fb6f7cb375786e4ca41b05b))
* **sensor:** enhance device status merging and preserve REST action deadlines ([72a42a5](https://github.com/stuartp44/hambrewclient/commit/72a42a59efea1ab3eef98029d2c76eb2a9f731aa))
* **sensor:** enhance sensor translations and ensure legacy unique IDs are preserved ([b811c68](https://github.com/stuartp44/hambrewclient/commit/b811c68ab1b6ef0114d69c193155f0b1f8d5fc54))
* **sensor:** enhance telemetry source handling and improve diagnostics for action states ([36d18b2](https://github.com/stuartp44/hambrewclient/commit/36d18b2dd0bbf45c68f4264a7784267055497699))
* **sensor:** hide idf build suffix in device firmware ([6046a34](https://github.com/stuartp44/hambrewclient/commit/6046a3483c76bdb66fd77172793e6f05fb02dc9a))
* **sensor:** honor explicit session completion over cached metadata ([280babd](https://github.com/stuartp44/hambrewclient/commit/280babdab406f6eb06fb23e25fdc9f7c3f64b604))
* **sensor:** improve telemetry management and session handling in MiniBrew integration ([104c904](https://github.com/stuartp44/hambrewclient/commit/104c9046be1d220a21ca7f0a256be28cb99da307))

## [0.11.2](https://github.com/stuartp44/hambrewclient/compare/v0.11.1...v0.11.2) (2026-10-10)


### Fixed

* **sensor:** improve performance of sensor state reads and add tests ([889745e](https://github.com/stuartp44/hambrewclient/commit/889745e4c4af359229e18468e712dfe33e8ece5d))

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
