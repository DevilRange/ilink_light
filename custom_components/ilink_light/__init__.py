"""iLink (JieLi) BLE lights."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .ilink import IlinkLight

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LIGHT]

type IlinkConfigEntry = ConfigEntry[IlinkLight]


async def async_setup_entry(hass: HomeAssistant, entry: IlinkConfigEntry) -> bool:
    """Set up a lamp. It may be powered off at the wall: the entity stays unavailable until it advertises."""
    entry.runtime_data = IlinkLight(
        bluetooth.async_ble_device_from_address(hass, entry.data[CONF_ADDRESS], connectable=True)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IlinkConfigEntry) -> bool:
    """Unload a lamp and drop its BLE connection."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v2 (one entry holding a dict of devices, upstream format) to v3 (one entry per lamp)."""
    if entry.version == 2:
        devices = list(entry.data.get("devices", {}).values())
        if len(devices) != 1:
            _LOGGER.error(
                "Cannot migrate an iLink entry with %d lamps; remove it and add the lamps again", len(devices)
            )
            return False
        mac = devices[0]["mac"].upper()
        # keep the existing entity (entity_id, history, area) by moving its unique_id
        await er.async_migrate_entries(hass, entry.entry_id, lambda _e: {"new_unique_id": mac.lower()})
        hass.config_entries.async_update_entry(entry, data={CONF_ADDRESS: mac}, unique_id=mac, version=3)
    return entry.version == 3
