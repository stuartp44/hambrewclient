# Release Workflows

This project has three release workflows:

## 1. Automatic Release (Main Branch)

**File:** `.github/workflows/release.yml`

**Triggers:** Automatically on every push to `main` branch

**What it does:**
- Uses semantic-release to analyze commit messages
- Automatically determines version number based on commit types
- Creates and publishes release immediately
- Updates manifest.json version
- Generates changelog from commits

**Usage:** Just merge your PR to main with proper conventional commit messages.

---

## 2. Draft Release (Manual/Preview)

**File:** `.github/workflows/draft-release.yml`

**Triggers:**
- Manual workflow dispatch (create release on demand)
- Pushing preview/beta/alpha/rc tags

### Manual Trigger

Create a draft release manually from GitHub Actions:

1. Go to **Actions** tab in GitHub
2. Select **Draft Release** workflow
3. Click **Run workflow**
4. Enter version (e.g., `1.2.3` or `1.0.0-preview.1`)
5. Check "Mark as pre-release" if needed
6. Click **Run workflow**

The workflow will:
- Create a draft release (not published)
- Generate changelog from commits
- Update manifest.json
- Wait for you to review and publish

### Preview Tag Trigger

Create preview releases automatically by pushing a tag:

```bash
# Create a preview release
git tag v1.0.0-preview.1
git push origin v1.0.0-preview.1

# Create a beta release
git tag v1.0.0-beta.1
git push origin v1.0.0-beta.1

# Create an alpha release
git tag v1.0.0-alpha.1
git push origin v1.0.0-alpha.1

# Create a release candidate
git tag v1.0.0-rc.1
git push origin v1.0.0-rc.1
```

Any tag matching these patterns will create a draft pre-release.

### Review and Publish

After the draft is created:

1. Go to [Releases](https://github.com/stuartp44/hambrewclient/releases)
2. Find your draft release
3. Review the changelog and version
4. Edit if needed
5. Click **Publish release** when ready

---

## 3. PR Preview Release (Testing)

**File:** `.github/workflows/pr-preview-release PR Preview |
|---------|------------------|---------------|------------|
| **Trigger** | Push to main | Manual or preview tag | PR label or comment |
| **Version** | Auto-determined | Manually specified | Auto (PR-based) |
| **Status** | Published immediately | Draft | Draft |
| **Use Case** | Production releases | Manual control | PR testing |
| **Pre-release** | Based on version | Can be specified | Always true |
| **Best For** | Production | Reviews | Testing PRs

**Method 1: Label**
1. Open your PR
2. Add the `preview-release` label
3. Wait for workflow to complete
4. Check PR comments for download link

**Method 2: Comment**
1. Open your PR
2. Comment `/preview-release`
3. Wait for workflow to complete
4. Check PR comments for download link

### What Happens

The workflow will:
- Create a preview version (e.g., `1.0.0-preview.123.abc1234`)
- Build a release archive
- Create a draft pre-release
- Comment on PR with download instructions
- Add `preview-released` label

### Testing Preview

Share the preview release link with testers who can:
1. Download the zip file
2. Extract to `custom_components`
3. Restart Home Assistant
4. Test and report back on the PR

---

## Comparison

| Feature | Automatic Release | Draft Release |
|---------|------------------|---------------|
| **Trigger** | Push to main | Manual or preview tag |
| **Version** | Auto-determined | Manually specified |
| **Status** | Published immediately | Draft (requires manual publish) |
| **Use Case** | Production releases | Testing, previews, manual control |
| **Pre-release** | Based on version | Can be specified |

---

## Best Practices

### Use Automatic Release When:
- Merging production-ready code to main
- You trust semantic versioning from commits
- You want immediate release

### Use Draft Release When:
- Testing a release before publishing
- Creating preview/beta versions
- You want to review/edit relea

### Use PR Preview When:
- Testing changes in a PR before merging
- Getting feedback from testers
- Validating fixes in development
- ShTesting a PR with Preview Release

```bash
# Create your PR
git checkout -b fix/temperature-sensor
git commit -m "fix: correct temperature sensor readings"
git push origin fix/temperature-sensor

# Open PR on GitHub

# On the PR page, either:
# 1. Add the 'preview-release' label, OR
# 2. Comment: /preview-release

# Wait for the workflow to complete
# Download link will be posted in PR comments
# Share with testers
# Get feedback
# Merge when ready
```

### aring test builds with usersse notes
- Manual version control needed

---

## Example Workflows

### Creating a Preview Release

```bash
# Make your changes
git checkout -b feat/new-feature
git commit -m "feat: add new sensor"
git push origin feat/new-feature

# After PR review, create preview instead of merging
git tag v1.1.0-preview.1
git push origin v1.1.0-preview.1

# Review the draft release on GitHub
# Make any edits needed
# Publish when ready

# Then merge to main for official release
git checkout main
git merge feat/new-feature
git push origin main  # Triggers automatic release
```

### Manual Release with Custom Notes

```bash
# Trigger draft release via GitHub Actions UI
# Specify version: 2.0.0
# Add custom release notes after draft is created
# Publish when ready
```

---

## Version Patterns

### Semantic Versioning
- `1.0.0` - Major release
- `1.1.0` - Minor release (new features)
- `1.1.1` - Patch release (bug fixes)

### Pre-release Versions
- `1.0.0-preview.1` - Preview release
- `1.0.0-alpha.1` - Alpha release
- `1.0.0-beta.1` - Beta release
- `1.0.0-rc.1` - Release candidate

All pre-release versions are automatically marked as pre-releases on GitHub.
