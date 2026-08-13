"""Constants for the Server Updater integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "server_updater"

PLATFORMS = ["binary_sensor", "button"]

# Config / options keys not already provided by homeassistant.const
CONF_PRIVATE_KEY = "private_key"
CONF_PRIVATE_KEY_PASSPHRASE = "private_key_passphrase"
CONF_USE_SUDO = "use_sudo"
CONF_SUDO_PASSWORD = "sudo_password"
CONF_REFRESH_APT_CACHE = "refresh_apt_cache"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_PORT = 22
DEFAULT_USE_SUDO = True
DEFAULT_REFRESH_APT_CACHE = True

DEFAULT_SCAN_INTERVAL = timedelta(hours=6)
MIN_SCAN_INTERVAL_MINUTES = 15

CONNECT_TIMEOUT = 15
COMMAND_TIMEOUT = 60
UPDATE_TIMEOUT = 1800
REBOOT_TIMEOUT = 20

MAX_PACKAGE_ATTRIBUTE = 50

ATTR_UPDATE_COUNT = "update_count"
ATTR_PACKAGES = "packages"
ATTR_REBOOT_PACKAGES = "reboot_required_packages"
ATTR_OS_NAME = "os_name"
