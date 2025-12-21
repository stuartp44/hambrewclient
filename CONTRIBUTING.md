# Contributing to MiniBrew Home Assistant Integration

Thank you for your interest in contributing to this project! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Commit Message Format](#commit-message-format)
- [Pull Request Process](#pull-request-process)
- [Development Setup](#development-setup)
- [Testing](#testing)

## Code of Conduct

Please be respectful and constructive in all interactions with the project and community.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/hambrewclient.git`
3. Create a new branch: `git checkout -b feat/your-feature-name`
4. Make your changes
5. Commit your changes following the [Conventional Commits](#commit-message-format) format
6. Push to your fork: `git push origin feat/your-feature-name`
7. Create a Pull Request

## Commit Message Format

**This project enforces Conventional Commits format.** All commits MUST follow this format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type

Must be one of the following:

- **feat**: A new feature
- **fix**: A bug fix
- **docs**: Documentation only changes
- **style**: Changes that don't affect the meaning of the code (white-space, formatting, etc)
- **refactor**: A code change that neither fixes a bug nor adds a feature
- **perf**: A code change that improves performance
- **test**: Adding missing tests or correcting existing tests
- **build**: Changes that affect the build system or external dependencies
- **ci**: Changes to CI configuration files and scripts
- **chore**: Other changes that don't modify src or test files
- **revert**: Reverts a previous commit

### Scope (Optional)

The scope should be the name of the component affected (e.g., `sensor`, `config-flow`, `craft`, `keg`).

### Subject

The subject contains a succinct description of the change:

- Use the imperative, present tense: "change" not "changed" nor "changes"
- Don't capitalize the first letter
- No period (.) at the end

### Examples

#### Good commit messages:
```
feat(sensor): add battery level sensor for craft devices
fix(config-flow): correct validation error handling
docs: update installation instructions for HACS
refactor(sensor): simplify temperature sensor logic
perf(coordinator): reduce API polling frequency
```

#### Bad commit messages:
```
❌ Update stuff
❌ Fixed bug
❌ Added new feature
❌ WIP
❌ asdf
```

### Breaking Changes

Breaking changes must be indicated in the footer with `BREAKING CHANGE:`:

```
feat(api): change authentication method

BREAKING CHANGE: The authentication now requires API token instead of username/password.
Users need to generate API tokens from MiniBrew website.
```

## Pull Request Process

1. **PR Title**: Must follow Conventional Commits format (e.g., `feat: add new sensor type`)
2. **Description**: Use the PR template to provide detailed information
3. **Commits**: All commits must follow Conventional Commits format
4. **Tests**: Ensure your changes work with actual MiniBrew devices if possible
5. **Documentation**: Update README.md or other docs if needed

### Automated Checks

Your PR will be automatically checked for:

- ✅ Conventional Commits format in all commits
- ✅ PR title follows Conventional Commits format
- ✅ Code quality and linting (if configured)

**PRs that don't pass these checks will not be merged.**

## Development Setup

### Prerequisites

- Home Assistant development environment
- MiniBrew account with registered devices
- Python 3.11 or newer

### Setup

1. Install Home Assistant in development mode
2. Link this integration to your Home Assistant custom_components:
   ```bash
   ln -s /path/to/hambrewclient/custom_components/hahbrewclient \
         /path/to/homeassistant/config/custom_components/
   ```
3. Restart Home Assistant
4. Enable debug logging in `configuration.yaml`:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.minibrew: debug
       pymbrewclient: debug
   ```

## Testing

Before submitting a PR:

1. Test with actual MiniBrew devices if possible
2. Verify all sensors update correctly
3. Check Home Assistant logs for errors
4. Test configuration flow
5. Test options flow
6. Verify device info is correct

## Versioning and Releases

This project uses **semantic versioning** and automated releases:

- Version numbers are automatically generated based on commit messages
- `feat:` commits trigger a **minor** version bump (0.X.0)
- `fix:` and `perf:` commits trigger a **patch** version bump (0.0.X)
- `BREAKING CHANGE:` triggers a **major** version bump (X.0.0)
- Release notes are auto-generated from commit messages

**This is why following Conventional Commits format is mandatory.**

## Questions?

Feel free to open an issue for any questions or concerns.

Thank you for contributing! 🍺
