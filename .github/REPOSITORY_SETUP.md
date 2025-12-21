# GitHub Repository Setup for HACS

To complete the HACS validation, you need to configure your GitHub repository settings.

## Required Settings

### 1. Repository Description

Add a description to your GitHub repository:

1. Go to https://github.com/stuartp44/hambrewclient
2. Click the ⚙️ (gear icon) next to "About" on the right side
3. Add this description:
   ```
   MiniBrew Home Assistant Integration - Monitor and control MiniBrew Craft and Keg devices
   ```
4. Click "Save changes"

### 2. Repository Topics

Add the following topics to your repository:

1. In the same "About" section settings
2. Add these topics (one at a time):
   - `home-assistant`
   - `hacs`
   - `minibrew`
   - `home-assistant-integration`
   - `brewing`
   - `homeassistant`
3. Click "Save changes"

### 3. Verify HACS Validation

After making these changes, you can verify your repository with HACS:

```bash
# Run HACS validation locally (if you have the action installed)
act -j validate-hacs
```

Or simply push your changes and the GitHub Actions workflow will validate automatically.

## Complete Checklist

- [ ] Repository description added
- [ ] Repository topics added (at least `home-assistant` and `hacs`)
- [ ] `issue_tracker` added to manifest.json (already done)
- [ ] Repository is public
- [ ] Has a valid README.md
- [ ] Has a valid LICENSE file

## Reference

More information: https://hacs.xyz/docs/publish/include
