#!/bin/bash
# Setup script for MiniBrew development environment

set -e

echo "Setting up MiniBrew development environment..."
echo ""

# Check if we're in the right directory
if [ ! -f "custom_components/hahbrewclient/manifest.json" ]; then
    echo "Error: This script must be run from the repository root"
    exit 1
fi

# Install Git hooks
echo "Installing Git hooks..."
if [ -d ".git" ]; then
    ln -sf ../../scripts/commit-msg .git/hooks/commit-msg
    chmod +x .git/hooks/commit-msg
    echo "Git hooks installed (commit message validation)"
else
    echo "Warning: .git directory not found, skipping Git hooks"
fi

# Install Python dependencies (optional)
echo ""
echo "Python development dependencies..."
read -p "Do you want to install Python semantic-release tools? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v pip &> /dev/null; then
        pip install python-semantic-release
        echo "Python semantic-release installed"
    else
        echo "Warning: pip not found, skipping Python dependencies"
    fi
fi

# Install Node.js dependencies for commitlint (optional)
echo ""
echo "Node.js development dependencies..."
read -p "Do you want to install commitlint for local validation? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v npm &> /dev/null; then
        npm install --save-dev @commitlint/cli @commitlint/config-conventional
        echo "commitlint installed"
        echo "   Run 'npx commitlint --from HEAD~1' to validate your last commit"
    else
        echo "Warning: npm not found, skipping Node.js dependencies"
    fi
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Read CONTRIBUTING.md for contribution guidelines"
echo "  2. Create a new branch: git checkout -b feat/your-feature"
echo "  3. Make your changes"
echo "  4. Commit using Conventional Commits format"
echo "  5. Push and create a Pull Request"
echo ""
echo "Example commit:"
echo "  git commit -m 'feat(sensor): add new battery sensor'"
echo ""
echo "Happy coding!"
