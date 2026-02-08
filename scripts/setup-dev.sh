#!/usr/bin/env bash
# Full development environment setup for pdf2md-claude
#   - Creates Python virtual environment (if missing)
#   - Installs package in editable mode with dev dependencies
#   - Configures git hooks from .githooks/ directory
#
# Usage:
#   ./scripts/setup-dev.sh              # normal setup
#   ./scripts/setup-dev.sh --force|-f   # recreate .venv from scratch

set -e

# ── Colors ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ── Helpers ──────────────────────────────────────────────────────────
info()  { echo -e "  ${CYAN}→${NC} $*"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}!${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; exit 1; }

# ── Resolve project root ────────────────────────────────────────────
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || fail "Not inside a git repository"
cd "$GIT_ROOT"

VENV_DIR="$GIT_ROOT/.venv"
FORCE=false
[[ "${1:-}" == "--force" || "${1:-}" == "-f" ]] && FORCE=true

echo ""
echo -e "${BOLD}=== pdf2md-claude: Dev Environment Setup ===${NC}"

# ── 1. Python virtual environment ───────────────────────────────────
echo ""
echo -e "${BOLD}[1/3] Python virtual environment${NC}"

PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[[ -z "$PYTHON" ]] && fail "Python 3.11+ not found on PATH"

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VERSION%%.*}
PY_MINOR=${PY_VERSION##*.}
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    fail "Python ≥3.11 required (found $PY_VERSION)"
fi
info "Using $PYTHON ($PY_VERSION)"

if $FORCE && [[ -d "$VENV_DIR" ]]; then
    info "Removing existing .venv (--force)..."
    rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment in ./.venv/ ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

# ── 2. Install dependencies ─────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] Dependencies${NC}"

info "Upgrading pip..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet

info "Installing pdf2md-claude in editable mode with dev extras..."
"$VENV_DIR/bin/pip" install -e ".[dev]" --quiet

ok "All dependencies installed"

# Quick sanity check
"$VENV_DIR/bin/python" -c "import anthropic, pymupdf, colorlog; print('  ✓ Core imports OK')"

# ── 3. Git hooks ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/3] Git hooks${NC}"

if [[ -d "$GIT_ROOT/.githooks" ]]; then
    info "Configuring git to use .githooks/ directory..."
    git config core.hooksPath .githooks

    info "Making hooks executable..."
    chmod +x "$GIT_ROOT/.githooks"/*

    ok "Git hooks installed"
    for hook in "$GIT_ROOT/.githooks"/*; do
        if [[ -f "$hook" ]]; then
            echo "      - $(basename "$hook")"
        fi
    done
else
    warn "No .githooks/ directory found — skipping"
fi

# ── Done ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Setup complete!${NC}"
echo ""
echo "  Activate the venv:   source .venv/bin/activate"
echo "  Run tests:           ./.venv/bin/python -m pytest tests/ -v"
echo "  Convert a PDF:       ./.venv/bin/python -m pdf2md_claude samples/multi_page_table.pdf -v"
echo ""
echo -e "  ${YELLOW}Tip: Use 'git commit --no-verify' to skip pre-commit hooks.${NC}"
echo ""
