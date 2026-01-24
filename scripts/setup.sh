#!/usr/bin/env bash
#
# Multi-Agent Beads System - Setup Script
# Creates development environment with all prerequisites
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Header
echo ""
echo "========================================"
echo "  Multi-Agent Beads System Setup"
echo "========================================"
echo ""

# Step 1: Check prerequisites
info "Checking prerequisites..."

# Check for bd (beads CLI)
if ! command -v bd &> /dev/null; then
    error "bd (beads CLI) not found. Install from: https://github.com/anthropics/beads"
fi
success "bd found: $(bd --version 2>/dev/null || echo 'version unknown')"

# Check for uv (Python package manager)
if ! command -v uv &> /dev/null; then
    error "uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
success "uv found: $(uv --version)"

# Check for claude (Claude Code CLI) - optional but recommended
if command -v claude &> /dev/null; then
    success "claude found"
else
    warn "claude (Claude Code) not found - optional but recommended for agent spawning"
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]); then
    error "Python 3.11+ required. Found: $PYTHON_VERSION"
fi
success "Python $PYTHON_VERSION found"

# Step 2: Navigate to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
info "Working in: $PROJECT_ROOT"

# Step 3: Create virtual environment and install dependencies
info "Setting up Python environment..."

if [ -d ".venv" ]; then
    info "Virtual environment already exists, syncing dependencies..."
else
    info "Creating virtual environment..."
fi

uv sync
success "Dependencies installed"

# Step 4: Initialize beads if needed
info "Checking beads initialization..."

if [ -d ".beads" ] && [ -f ".beads/beads.db" ]; then
    success "Beads already initialized"
else
    info "Initializing beads..."
    bd init
    success "Beads initialized"
fi

# Step 5: Verify installation
info "Verifying installation..."

# Check that dashboard module is importable
if uv run python -c "import dashboard" 2>/dev/null; then
    success "Dashboard module importable"
else
    warn "Dashboard module not importable (may need PYTHONPATH setup)"
fi

# Check that tests can be discovered
TEST_COUNT=$(uv run pytest --collect-only -q 2>/dev/null | grep -c "test" || echo "0")
if [ "$TEST_COUNT" -gt 0 ]; then
    success "Found $TEST_COUNT tests"
else
    warn "No tests found or pytest error"
fi

# Final success message
echo ""
echo "========================================"
echo -e "${GREEN}  Setup Complete!${NC}"
echo "========================================"
echo ""
echo "Quick start commands:"
echo "  bd ready                    # Find work to do"
echo "  bd list                     # List all issues"
echo "  uv run pytest               # Run tests"
echo "  uv run python -m dashboard.app  # Start dashboard"
echo "  python scripts/monitor.py   # Monitor agents"
echo ""
echo "For agent spawning:"
echo "  python scripts/spawn_agent.py developer --instance 1"
echo ""
