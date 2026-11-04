"""Constants for the Server Updater integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "server_updater"

PLATFORMS = ["binary_sensor", "button", "sensor", "switch", "update"]

# Config / options keys not already provided by homeassistant.const
CONF_PRIVATE_KEY = "private_key"
CONF_PRIVATE_KEY_PASSPHRASE = "private_key_passphrase"
CONF_USE_SUDO = "use_sudo"
CONF_SUDO_PASSWORD = "sudo_password"
CONF_REFRESH_APT_CACHE = "refresh_apt_cache"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_NOTIFY_ON_FAILURE = "notify_on_failure"
# The server's pinned SSH host key (OpenSSH public key format).
CONF_HOST_KEY = "host_key"
# Config flow only: confirm a changed host key shown to the user.
CONF_ACCEPT_HOST_KEY = "accept_new_host_key"

DEFAULT_PORT = 22
DEFAULT_USE_SUDO = True
DEFAULT_REFRESH_APT_CACHE = True
DEFAULT_NOTIFY_ON_FAILURE = True

DEFAULT_SCAN_INTERVAL = timedelta(hours=6)
MIN_SCAN_INTERVAL_MINUTES = 15

CONNECT_TIMEOUT = 15
COMMAND_TIMEOUT = 60
UPDATE_TIMEOUT = 1800
REBOOT_TIMEOUT = 20
# After a reboot, wait before re-checking the server so the check doesn't
# run against the still shutting-down (or not yet booted) system.
REBOOT_REFRESH_DELAY = 180
# If the server isn't back (reachable, booted anew, autostart guests running)
# at the first check, keep re-checking at this interval until the timeout.
REBOOT_RETRY_INTERVAL = 60
REBOOT_WATCH_TIMEOUT = 900
CONTAINER_RESTART_TIMEOUT = 180
NEEDRESTART_TIMEOUT = 120
SERVICE_RESTART_TIMEOUT = 300
# Wait before retrying a dist-upgrade whose package downloads failed, giving
# a mirror that is mid-sync a moment to catch up.
APT_FETCH_RETRY_DELAY = 30
REGISTRY_TIMEOUT = 15

MAX_PACKAGE_ATTRIBUTE = 50
MAX_REBOOT_PACKAGES_IN_ISSUE = 10

ATTR_REBOOT_PACKAGES = "reboot_required_packages"
ATTR_RUNNING_KERNEL = "running_kernel"
ATTR_NEWEST_KERNEL = "newest_kernel"
ATTR_SERVICES = "services"
ATTR_EXCLUDED_SERVICES = "excluded_services"
ATTR_SERVICES_REQUIRING_REBOOT = "services_requiring_reboot"
ATTR_GUESTS = "guests"
ATTR_REBOOTING = "rebooting"

# Fired for every action (update, reboot, container/service operations) with
# its outcome, so automations can e.g. forward failures to a phone.
EVENT_SERVER_UPDATER = f"{DOMAIN}_event"

# Services needrestart may list but that must not be restarted automatically:
# restarting them would end sessions (dbus, logind, getty, user@), stop all
# containers (docker, containerd) or touch the guests' lifecycle on Proxmox VE.
SERVICE_RESTART_EXCLUDE_PATTERNS = (
    r"dbus.*",
    r"systemd-.*",
    r"user@.*",
    r"(serial-)?getty@.*",
    r"(gdm|lightdm|sddm|display-manager).*",
    r"(docker|containerd)\.service",
    r"pve-guests\.service",
    r"(lxc|lxcfs|qemu-server|pve-ha-.*)(@.*)?\.service",
)

SERVICE_RUN_MAINTENANCE = "run_maintenance"
REBOOT_NEVER = "never"
REBOOT_IF_REQUIRED = "if_required"
REBOOT_ALWAYS = "always"
