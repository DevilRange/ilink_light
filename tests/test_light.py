"""Light entity: lamp quirks (command order), mode detection, availability."""

from unittest.mock import patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    DOMAIN as LIGHT,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ilink_light import ilink
from custom_components.ilink_light.const import DOMAIN

from .conftest import ADDRESS

ENTITY = "light.ilink"


@pytest.fixture
async def fake(hass: HomeAssistant, lamp):
    entry = MockConfigEntry(domain=DOMAIN, title="iLink", unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}, version=3)
    entry.add_to_hass(hass)
    with patch("custom_components.ilink_light.light.COLOR_FADE", 0):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        yield lamp["lamp"]


async def turn_on(hass: HomeAssistant, fake, **data) -> list[bytes]:
    fake.sent.clear()
    await hass.services.async_call(LIGHT, "turn_on", {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    return [f for batch in fake.sent for f in batch]


async def poll(hass: HomeAssistant, fake, status: ilink.Status) -> None:
    fake.state = status
    await hass.data[LIGHT].get_entity(ENTITY)._poll()


async def test_initial_state_is_power_up_mode(hass: HomeAssistant, fake) -> None:
    state = hass.states.get(ENTITY)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "White: cold"
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 6000


async def test_rgb_sends_brightness_after_the_fade(hass: HomeAssistant, fake) -> None:
    fake.state = ilink.Status(True, 100, (255, 0, 0), 0, 0, None)
    await turn_on(hass, fake, **{ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 100})
    # separate writes with the fade in between; back to back would leave the LEDs dark
    assert fake.sent == [[ilink.rgb(255, 0, 0)], [ilink.brightness(100)]]
    assert hass.states.get(ENTITY).attributes[ATTR_RGB_COLOR] == (255, 0, 0)


async def test_kelvin_sends_brightness_first(hass: HomeAssistant, fake) -> None:
    frames = await turn_on(hass, fake, **{ATTR_COLOR_TEMP_KELVIN: 3500, ATTR_BRIGHTNESS: 80})
    assert frames == [ilink.brightness(80), ilink.kelvin(3500)]


async def test_modes(hass: HomeAssistant, fake) -> None:
    assert await turn_on(hass, fake, **{ATTR_EFFECT: "White: sunlight (max)"}) == [
        ilink.brightness(255),
        ilink.preset(3),
    ]
    assert await turn_on(hass, fake, **{ATTR_EFFECT: "White: night light"}) == [ilink.brightness(30), ilink.preset(3)]
    assert await turn_on(hass, fake, **{ATTR_EFFECT: "Rainbow"}) == [ilink.scene(1)]


@pytest.mark.parametrize(
    ("status", "effect"),
    [
        (ilink.Status(True, 255, (0, 0, 0), 0x1E, 0x1E, None), "White: night light"),  # remote's night
        (ilink.Status(True, 30, (0, 0, 0), 0xFF, 0xFF, None), "White: night light"),  # HA's night
        (ilink.Status(True, 255, (0, 0, 0), 0xFF, 0xFF, None), "White: sunlight (max)"),
        (ilink.Status(True, 255, (0, 0, 0), 0, 0xFF, None), "White: warm"),
        (ilink.Status(True, 255, (0, 0, 0), 135, 120, None), None),  # warm/cold buttons
        (ilink.Status(True, 200, (9, 9, 9), 0, 0, 10), "Feel Green"),
    ],
)
async def test_remote_changes_are_reflected(hass: HomeAssistant, fake, status, effect) -> None:
    await poll(hass, fake, status)
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] == effect


async def test_plain_on_restores_last_state(hass: HomeAssistant, fake) -> None:
    await poll(hass, fake, ilink.Status(True, 90, (0, 0, 0), 85, 170, None))  # 4000 K at 90
    fake.state = ilink.Status(False, 90, (0, 0, 0), 85, 170, None)
    await hass.services.async_call(LIGHT, "turn_off", {ATTR_ENTITY_ID: ENTITY}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ENTITY).state == STATE_OFF
    fake.state = ilink.Status(True, 90, (0, 0, 0), 85, 170, None)
    assert await turn_on(hass, fake) == [ilink.ON, ilink.brightness(90), ilink.kelvin(4000)]


async def test_power_cut_and_back(hass: HomeAssistant, fake) -> None:
    await poll(hass, fake, ilink.Status(True, 255, (0, 0, 0), 255, 255, None))  # sunlight (max) before the cut
    fake.connected = False
    fake.on_disconnect()
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    seen = []
    hass.bus.async_listen(
        "state_changed",
        lambda e: seen.append((e.data["new_state"].state, e.data["new_state"].attributes.get(ATTR_EFFECT))),
    )
    fake.connected = True
    await poll(hass, fake, ilink.Status(True, 255, (0, 0, 0), 255, 0, None))  # lamp powers up cold
    await hass.async_block_till_done()
    # one write, straight from unavailable to the power-up state: automations on that transition see fresh attributes
    assert seen == [(STATE_ON, "White: cold")]


async def test_command_error_is_translated(hass: HomeAssistant, fake) -> None:
    fake.fail = True
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(LIGHT, "turn_off", {ATTR_ENTITY_ID: ENTITY}, blocking=True)
    assert err.value.translation_key == "command_failed"
