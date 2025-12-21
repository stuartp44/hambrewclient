# Git Hooks Setup

This directory contains Git hooks to help maintain code quality and enforce commit message standards.

## Installation

To enable the commit message validation hook locally:

```bash
# From the repository root
ln -s ../../scripts/commit-msg .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```

## Available Hooks

### commit-msg

Validates that commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/) specification.

**What it checks:**
- Commit message starts with a valid type (feat, fix, docs, etc.)
- Optional scope in parentheses
- Colon and space after type/scope
- Subject doesn't start with uppercase
- Subject is not empty

**Example valid messages:**
```
feat: add new sensor type
fix(config-flow): handle connection timeout
docs: update installation guide
```

**Example invalid messages:**
```
Update stuff
Fixed a bug
WIP
```

## Why Use Git Hooks?

Git hooks provide **immediate feedback** during development, catching issues before you push to the remote repository. This saves time by preventing CI/CD failures.

## Note

Even with local hooks installed, the CI/CD pipeline will validate all commits when you create a pull request, ensuring consistency across all contributors.
