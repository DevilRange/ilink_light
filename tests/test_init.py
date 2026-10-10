"""Setup, unload and migration from the upstream (v2) entry format."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ilink_light.const import DOMAIN

from .conftest import ADDRESS


async def test_setup_and_unload(hass: HomeAssistant, lamp) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}, version=3)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not lamp["lamp"].connected


async def test_migrate_v2_keeps_entity(hass: HomeAssistant, entity_registry: er.EntityRegistry, lamp) -> None:
    mac = ADDRESS.lower()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="iLink Light",
        version=2,
        data={"devices": {mac: {"mac": mac, "name": "ilink_light", "scan_interval": 300, "scan_interval_fast": 5}}},
    )
    entry.add_to_hass(hass)
    old = entity_registry.async_get_or_create(
        "light", DOMAIN, f"{mac}-ilink_light Light", config_entry=entry, suggested_object_id="ilink_light"
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.version == 3
    assert entry.unique_id == ADDRESS
    assert entry.data == {CONF_ADDRESS: ADDRESS}
    assert entity_registry.async_get(old.entity_id).unique_id == mac
    assert old.entity_id == "light.ilink_light"


async def test_migrate_v2_multiple_lamps_fails(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, data={"devices": {"a": {"mac": "a"}, "b": {"mac": "b"}}})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
