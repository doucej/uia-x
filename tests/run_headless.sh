#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Headless test harness for UIA-X Linux AT-SPI2 backend.
#
# Launches Xvfb + a D-Bus session + AT-SPI2 registry, optionally starts a
# test application, and runs the given command (default: pytest).
#
# Usage:
#   ./tests/run_headless.sh                          # run all tests
#   ./tests/run_headless.sh pytest tests/test_linux_integration.py -v
#   UIAX_TEST_APP=gedit ./tests/run_headless.sh pytest -k integration
#
# Options (environment variables):
#   DISPLAY_NUM   – X display number to use (default: 99)
#   SCREEN_SIZE   – Xvfb screen geometry   (default: 1920x1080x24)
#   UIAX_TEST_APP – Application to launch before tests (default: xterm)
#   UIAX_RUN_INTEGRATION – Set to 1 to enable integration tests
#
# Requirements:
#   - Xvfb              (apt: xvfb)
#   - dbus-run-session  (apt: dbus)
#   - at-spi2-core      (apt: at-spi2-core)
#   - python3-pyatspi   (apt: python3-pyatspi  or  gir1.2-atspi-2.0)
#   - xterm or gedit    (for integration test targets)
#   - xdotool           (apt: xdotool)  – optional fallback for keystroke injection
# ---------------------------------------------------------------------------
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN_SIZE="${SCREEN_SIZE:-1920x1080x24}"
UIAX_TEST_APP="${UIAX_TEST_APP:-}"
export UIAX_RUN_INTEGRATION="${UIAX_RUN_INTEGRATION:-1}"
export DISPLAY=":${DISPLAY_NUM}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cleanup() {
    echo "[headless] Cleaning up..."
    # Kill the test app if we started one
    if [[ -n "${APP_PID:-}" ]]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    # Kill Xvfb
    if [[ -n "${XVFB_PID:-}" ]]; then
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Start Xvfb
# ---------------------------------------------------------------------------
echo "[headless] Starting Xvfb on display ${DISPLAY} (${SCREEN_SIZE})..."
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_SIZE}" -ac +extension RANDR &
XVFB_PID=$!
sleep 1

# Verify Xvfb is running
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "[headless] ERROR: Xvfb failed to start" >&2
    exit 1
fi
echo "[headless] Xvfb running (PID ${XVFB_PID})"

# ---------------------------------------------------------------------------
# 2. Run everything inside a D-Bus session
# ---------------------------------------------------------------------------
# The inner script sets up AT-SPI2, launches the test app, and runs tests.
exec dbus-run-session -- bash -c '
    set -euo pipefail

    echo "[headless] D-Bus session active: ${DBUS_SESSION_BUS_ADDRESS:-<not set>}"

    # Enable accessibility (needed for AT-SPI2)
    export GTK_MODULES="${GTK_MODULES:+$GTK_MODULES:}gail:atk-bridge"
    export QT_ACCESSIBILITY=1
    export QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1

    # Start AT-SPI2 registryd (if not auto-started by dbus-activation)
    if command -v /usr/libexec/at-spi2-registryd &>/dev/null; then
        /usr/libexec/at-spi2-registryd &
        sleep 0.5
        echo "[headless] AT-SPI2 registryd started"
    elif command -v /usr/lib/at-spi2-core/at-spi2-registryd &>/dev/null; then
        /usr/lib/at-spi2-core/at-spi2-registryd &
        sleep 0.5
        echo "[headless] AT-SPI2 registryd started"
    else
        echo "[headless] WARNING: at-spi2-registryd not found; relying on dbus-activation"
    fi

    # Start at-spi-bus-launcher if present
    if command -v /usr/libexec/at-spi-bus-launcher &>/dev/null; then
        /usr/libexec/at-spi-bus-launcher --launch-immediately &
        sleep 0.5
    fi

    # Launch test application (if specified)
    APP_PID=""
    UIAX_TEST_APP="${UIAX_TEST_APP:-}"
    if [[ -n "${UIAX_TEST_APP}" ]]; then
        echo "[headless] Launching test app: ${UIAX_TEST_APP}"
        ${UIAX_TEST_APP} &
        APP_PID=$!
        sleep 2
        echo "[headless] Test app running (PID ${APP_PID})"
    fi

    # Run the user-specified command or default to pytest
    cd "'"${PROJECT_DIR}"'"
    if [[ $# -gt 0 ]]; then
        echo "[headless] Running: $*"
        "$@"
    else
        echo "[headless] Running: pytest tests/ -v"
        pytest tests/ -v
    fi
    EXIT_CODE=$?

    # Cleanup test app
    if [[ -n "${APP_PID}" ]]; then
        kill "${APP_PID}" 2>/dev/null || true
    fi

    exit ${EXIT_CODE}
' _ "$@"
