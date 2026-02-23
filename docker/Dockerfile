# ---------------------------------------------------------------------------
# UIA-X  Linux AT-SPI2 CI / Demo Container
# ---------------------------------------------------------------------------
# Provides a headless Xvfb + AT-SPI2 environment for running both:
#   • Unit tests   (mock-based, no live session required)
#   • Integration tests  (gnome-calculator inside Xvfb)
#
# Build:
#   docker build -t uiax-linux -f docker/Dockerfile .
#
# Run unit tests:
#   docker run --rm uiax-linux pytest tests/test_linux_backend.py -v
#
# Run integration tests (calculator demo):
#   docker run --rm uiax-linux ./tests/run_headless.sh \
#       pytest tests/test_linux_integration_docker.py -v
#
# Run ALL tests (Windows mock + Linux unit + Linux integration):
#   docker run --rm uiax-linux ./tests/run_headless.sh \
#       pytest tests/ -v --ignore=tests/test_linux_integration.py
#
# Interactive shell:
#   docker run --rm -it uiax-linux bash
# ---------------------------------------------------------------------------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System packages: X11 virtual framebuffer, AT-SPI2, GTK apps for testing
RUN apt-get update && apt-get install -y --no-install-recommends \
        # Python
        python3 python3-pip python3-venv \
        # AT-SPI2 accessibility stack
        python3-pyatspi gir1.2-atspi-2.0 at-spi2-core python3-gi \
        # D-Bus (needed for AT-SPI2 session bus)
        dbus dbus-x11 \
        # Virtual display
        xvfb \
        # Keystroke / mouse fallback
        xdotool \
        # Test target application
        gnome-calculator \
        # Useful for debugging (optional, small)
        procps xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (AT-SPI2 works better this way)
RUN useradd -m -s /bin/bash uiax
USER uiax
WORKDIR /home/uiax/uia-x

# Install Python dependencies into user site-packages
# (pip install --user avoids needing to be root)
COPY --chown=uiax:uiax requirements.txt .
RUN pip install --user --break-system-packages -r requirements.txt

# Copy project
COPY --chown=uiax:uiax . .

# Make helper scripts executable
RUN chmod +x tests/run_headless.sh

# Default: run unit tests (no display required)
CMD ["python3", "-m", "pytest", "tests/test_linux_backend.py", "tests/test_tools.py", "-v"]
