"""Diagnostics: connection state and the lamp's last raw status."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import IlinkConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: IlinkConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    lamp = entry.runtime_data
    return {
        "entry": entry.as_dict(),
        "seen": lamp.ble_device is not None,
        "connected": lamp.connected,
        "last_status": asdict(lamp.status) if lamp.status else None,
        "last_reply": lamp.last_reply.hex() if lamp.last_reply else None,
    }
