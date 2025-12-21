# GitHub Copilot Custom Instructions for hambrewclient

## Project Overview
This is a **Home Assistant custom component** for integrating MiniBrew Craft and Keg devices. It's an unofficial, community-developed integration with no affiliation to MiniBrew B.V.

## Critical Requirements

### 1. Conventional Commits - MANDATORY
- **ALL commits MUST follow Conventional Commits format**: `<type>(<scope>): <subject>`
- Valid types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
- This is ENFORCED by CI/CD and will block merges
- Example: `feat(sensor): add battery level monitoring`
- No emojis in commit messages or code

### 2. Project Structure
- Domain: `minibrew` (NOT `hahbrewclient` - old directory was removed)
- Location: `custom_components/minibrew/`
- Key files:
  - `manifest.json` - Must have keys in order: domain, name, then alphabetical
  - `__init__.py` - Has CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
  - `config_flow.py` - Config flow implementation
  - `sensor.py` - Sensor entities for Craft and Keg devices
  - `const.py` - Constants (DOMAIN, MANUFACTURER)

### 3. Release Automation
**Three release workflows:**

1. **Automatic Release** (`.github/workflows/release.yml`)
   - Triggers on push to main
   - Uses semantic-release to determine version from commits
   - Waits for all validation checks to pass before releasing
   - Updates manifest.json version automatically

2. **Draft Release** (`.github/workflows/draft-release.yml`)
   - Manual workflow dispatch or preview tags (v*-preview*, v*-beta*, v*-alpha*, v*-rc*)
   - Creates draft releases for review
   - Includes contributor acknowledgments

3. **PR Preview Release** (`.github/workflows/pr-preview-release.yml`)
   - Triggered by `preview-release` label or `/preview-release` comment on PR
   - Creates preview builds for testing
   - Version format: `{version}-preview.{pr-number}.{sha}`

### 4. Validation & Quality
**All code changes must pass:**
- HACS validation
- Home Assistant hassfest validation
- Manifest.json structure validation
- Conventional commits validation
- Release workflow waits for these checks

### 5. Code Standards
- Python 3.11+
- Home Assistant 2023.1+
- Uses `pymbrewclient>=1.0.10` library
- Cloud polling integration type
- Config flow only (no YAML configuration)

### 6. Documentation Requirements
**README.md must include:**
- Prominent disclaimer: Not affiliated with MiniBrew B.V.
- MiniBrew Pro subscription requirement notice
- Installation instructions (HACS and manual)
- Feature list for Craft and Keg devices
- Troubleshooting section
- Contributor acknowledgment (including GitHub Copilot assistance)

### 7. Contributor Management
- All releases include contributor acknowledgments
- Extracted from git commit authors
- Listed in CHANGELOG.md and release notes
- Custom template: `.github/release_templates/CHANGELOG.md.j2`

### 8. Dependencies & Tools
- **Dependabot** configured for GitHub Actions and Python dependencies
- **Semantic-release** for version management
- **Commitlint** for commit message validation
- **Git hooks** for local validation (scripts/commit-msg)

### 9. Style Guidelines
- **No emojis** in code, workflows, or documentation
- Clean, professional tone
- Clear error messages
- Comprehensive logging

### 10. Key Files to Never Modify Without Care
- `manifest.json` - Must maintain exact structure and ordering
- `pyproject.toml` - Semantic-release configuration
- `.commitlintrc.json` - Commit validation rules
- Workflow files - Critical for CI/CD

## Common Tasks

### Adding a New Sensor
1. Add to `sensor.py` following existing patterns
2. Commit: `feat(sensor): add {sensor-name} sensor`
3. Update README if user-facing

### Fixing a Bug
1. Make fix in appropriate file
2. Commit: `fix({scope}): {description}`
3. Add test if possible

### Updating Documentation
1. Edit README.md or other docs
2. Commit: `docs: {description}`

### Creating a Preview Release
1. Add `preview-release` label to PR, OR
2. Comment `/preview-release` on PR
3. Test the generated preview build

## Important Notes
- MiniBrew Pro subscription is **required** for this integration to work
- Integration is cloud polling (no local API)
- Supports both Craft (brewing) and Keg (dispensing) devices
- Uses DataUpdateCoordinator for efficient updates
- Default refresh interval: 60 seconds (configurable)

## Disclaimer Template
Always include when relevant:
> This is an unofficial, community-developed integration. It is not affiliated with, endorsed by, officially maintained by, or in any way officially connected to MiniBrew B.V. or any of its subsidiaries or affiliates.

## Workflow Patterns
- Main branch is protected
- All changes via PRs
- PRs require conventional commit titles
- Auto-release on merge to main
- Draft releases for manual review
- Preview releases for PR testing

## Development Setup
- Run `./scripts/setup-dev.sh` for environment setup
- Installs git hooks for commit validation
- Optional: semantic-release and commitlint tools

## When Making Changes
1. Always follow conventional commits
2. Update relevant documentation
3. Ensure validation checks pass
4. Consider if README needs updating
5. Check if manifest.json needs version bump (auto-handled by semantic-release)
6. No emojis anywhere

## File Paths Reference
- Integration: `custom_components/minibrew/`
- Workflows: `.github/workflows/`
- Docs: `README.md`, `CONTRIBUTING.md`, `.github/RELEASE_WORKFLOWS.md`
- Config: `pyproject.toml`, `.commitlintrc.json`, `.github/dependabot.yml`
- Scripts: `scripts/commit-msg`, `scripts/setup-dev.sh`
