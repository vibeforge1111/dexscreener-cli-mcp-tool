#!/usr/bin/env bash
set -e

echo ""
echo "  ===================================="
echo "   Dexscreener CLI - Quick Install"
echo "  ===================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  [ERROR] Python 3 not found. Install Python 3.11+ first."
    exit 1
fi

# Create venv if missing
if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate and install
echo "  Installing dependencies..."
source .venv/bin/activate
pip install -e . --quiet

# Symlink entry points to a directory on PATH
LINK_DIR="$HOME/.local/bin"
mkdir -p "$LINK_DIR"

VENV_BIN="$(cd "$(dirname "$0")" && pwd)/.venv/bin"

ln -sf "$VENV_BIN/ds" "$LINK_DIR/ds"
ln -sf "$VENV_BIN/dexscreener-mcp" "$LINK_DIR/dexscreener-mcp"

echo ""
echo "  ===================================="
echo "   Install complete!"
echo "  ===================================="
echo ""

# Check if LINK_DIR is on PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$LINK_DIR"; then
    echo "  [NOTE] Add ~/.local/bin to your PATH:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "  Add that line to your ~/.zshrc or ~/.bashrc, then restart your shell."
    echo ""
fi

echo "  Quick start:"
echo "    ds setup          - Calibrate your scanner"
echo "    ds hot            - Scan hot tokens"
echo "    ds watch          - Live dashboard"
echo "    ds search pepe    - Search tokens"
echo "    ds --help         - All commands"
echo ""
echo "  MCP server:"
echo "    dexscreener-mcp   - Start MCP server (stdio)"
echo ""
