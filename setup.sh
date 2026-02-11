#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="daily email inbox checking agent (POC)"

echo "==> Setting up: ${PROJECT_NAME}"
echo

# Python discovery
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ and try again."
  exit 1
fi

PY_VER=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "==> Using Python: $PYTHON_BIN (version $PY_VER)"

# Create venv 
if [ ! -d ".venv" ]; then
  echo "==> Creating virtual environment in .venv"
  "$PYTHON_BIN" -m venv .venv
else
  echo "==> Virtual environment already exists (.venv)"
fi

# Activate venv 
# shellcheck disable=SC1091
source .venv/bin/activate
echo "==> Activated virtual environment: $(python -c 'import sys; print(sys.executable)')"

# Install dependencies
if [ -f "requirements.txt" ]; then
  echo "==> Installing dependencies from requirements.txt"
  pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
else
  echo "ERROR: requirements.txt not found."
  exit 1
fi

# Env file bootstrap 
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    echo "==> Creating .env from .env.example"
    cp .env.example .env
    echo "NOTE: Please edit .env and set COHERE_API_KEY (and optionally DRY_RUN, GOOGLE_OAUTH_CREDENTIALS)."
  else
    echo "==> Creating a minimal .env (since .env.example not found)"
    cat > .env <<'EOF'
COHERE_API_KEY=
DRY_RUN=0
GOOGLE_OAUTH_CREDENTIALS=credentials.json
EOF
    echo "NOTE: Please edit .env and set COHERE_API_KEY."
  fi
else
  echo "==> .env already exists (leaving as-is)"
fi

# Reminders for more seamless setup 
echo
echo "==> Setup complete."
echo
echo "Next steps:"
echo "  1) Put your Google OAuth file in place (NOT committed):"
echo "       - default: ./credentials.json"
echo "       - or set GOOGLE_OAUTH_CREDENTIALS to a custom path in .env"
echo "  2) Edit ./profile.txt with your user fact profile"
echo "  3) Run the agent:"
echo "       source .venv/bin/activate"
echo "       python agent.py"
echo
echo "Tip: Run in dry mode (no Gmail drafts created):"
echo "       export DRY_RUN=1"
echo "       python agent.py"