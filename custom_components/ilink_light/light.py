"""iLink light entity. State always comes from the lamp's own status reply."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import IlinkConfigEntry, ilink
from .const import DOMAIN
from .scenes import all_scenes

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0  # one lamp per entry; commands are serialised by IlinkLight's lock
POLL_INTERVAL = timedelta(seconds=3)  # picks up IR remote changes; one ~0.1 s read on an open link
COLOR_FADE = 1.0  # s; the RGB fade takes ~0.6 s and any later command would cut it short
SCENE_IDS = {name: i for i, name in all_scenes.items()}
# White modes as effects, in the remote's MODE-button order (cold is what the lamp powers up in),
# then app-only presets: name -> (0809 preset level, cold, warm). Night (30/30) has no BLE preset; HA
# sends max white at brightness NIGHT_BRI instead, which is the same light output.
PRESETS = {
    "White: cold": (1, 0xFF, 0x00),
    "White: warm": (5, 0x00, 0xFF),
    "White: sunlight (max)": (3, 0xFF, 0xFF),
    "White: night light": (3, 0x1E, 0x1E),
    "White: nature light": (2, 0xB4, 0x64),
    "White: sunset": (4, 0x4B, 0xC8),
}
PRESET_BY_PAIR = {(c, w): name for name, (_, c, w) in PRESETS.items()}
NIGHT, NIGHT_BRI = "White: night light", 0x1E


async def async_setup_entry(
    hass: HomeAssistant, entry: IlinkConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the light for a config entry."""
    async_add_entities([IlinkLightEntity(entry.runtime_data, entry.data[CONF_ADDRESS])])


class IlinkLightEntity(LightEntity):
    """An iLink lamp."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_available = False
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_min_color_temp_kelvin = ilink.MIN_KELVIN
    _attr_max_color_temp_kelvin = ilink.MAX_KELVIN
    _attr_effect_list = [*PRESETS, *SCENE_IDS]  # RGB button = scenes 1-10 in order

    def __init__(self, lamp: ilink.IlinkLight, address: str) -> None:
        self._lamp = lamp
        self._address = address
        self._last_on: ilink.Status | None = None  # restored on a plain turn_on
        self._polling = False
        self._repoll = False
        self._adv_time: float | None = None
        self._attr_unique_id = address.lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="iLink (JieLi)",
        )

    async def async_added_to_hass(self) -> None:
        self._lamp.on_disconnect = self._on_disconnect
        self.async_on_remove(
            bluetooth.async_register_callback(
                self.hass,
                self._on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self._address, connectable=True),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        self.async_on_remove(
            bluetooth.async_track_unavailable(self.hass, self._on_gone, self._address, connectable=True)
        )
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, POLL_INTERVAL))
        if self._lamp.ble_device and bluetooth.async_address_present(self.hass, self._address, connectable=True):
            self._schedule_poll()

    async def async_will_remove_from_hass(self) -> None:
        self._lamp.on_disconnect = None

    @callback
    def _on_advertisement(self, info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange) -> None:
        # Advertising means powered and not connected: (re)connect and read what it booted into.
        self._lamp.ble_device = info.device
        if not self._lamp.connected:
            self._schedule_poll()

    @callback
    def _on_disconnect(self) -> None:
        # Mains-powered and nobody else connects, so a dropped link means the wall switch cut power.
        # A glitch heals itself: the lamp re-advertises and _on_advertisement reconnects.
        self._set_available(False)

    @callback
    def _on_gone(self, _info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if not self._lamp.connected:  # it never advertises while we're connected
            self._set_available(False)

    @callback
    def _set_available(self, available: bool) -> None:
        if self._attr_available != available:
            self._attr_available = available
            if not available:
                _LOGGER.info("%s is unavailable (powered off at the wall?)", self._address)
            self.async_write_ha_state()

    @callback
    def _schedule_poll(self) -> None:
        if self._polling:
            self._repoll = True  # a poll is mid-flight; read again once it finishes
        else:
            self._polling = True
            self.hass.async_create_background_task(self._poll(), f"{DOMAIN} poll {self._address}")

    @callback
    def _tick(self, _now: object = None) -> None:
        # Connected: poll to catch IR remote changes. Not connected: reconnect only on a fresh
        # advert (HA drops repeat identical adverts, so _on_advertisement misses a plain link drop).
        if self._lamp.connected:
            self._schedule_poll()
        elif (
            info := bluetooth.async_last_service_info(self.hass, self._address, connectable=True)
        ) and info.time != self._adv_time:
            self._adv_time = info.time
            self._lamp.ble_device = info.device
            self._schedule_poll()

    async def _poll(self) -> None:
        try:
            while True:
                self._repoll = False
                try:
                    status = await self._lamp.settled()
                except Exception as err:
                    _LOGGER.debug("%s: poll failed: %s", self._address, err)
                    self._set_available(False)
                    return
                if not self._attr_available:  # one state write together with the new status, no "unknown" blip
                    _LOGGER.info("%s is reachable again", self._address)
                    self._attr_available = True
                self._update(status)
                if not self._repoll:
                    return
        finally:
            self._polling = False

    @callback
    def _update(self, s: ilink.Status | None) -> None:
        if s is not None:
            self._attr_is_on = s.on
            if s.scene is not None:  # rgb/brightness animate during a scene; keep the last steady values
                self._attr_effect = all_scenes.get(s.scene)
                self._attr_color_mode = ColorMode.RGB
            else:
                self._attr_effect = PRESET_BY_PAIR.get((s.cold, s.warm))
                if (s.cold, s.warm, s.brightness) == (0xFF, 0xFF, NIGHT_BRI):  # HA's own night light
                    self._attr_effect = NIGHT
                self._attr_brightness = s.brightness
                self._attr_rgb_color = s.rgb
                self._attr_color_temp_kelvin = s.kelvin
                self._attr_color_mode = ColorMode.COLOR_TEMP if s.kelvin else ColorMode.RGB
            if s.on:
                self._last_on = s
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        was_on = bool(self.is_on)
        bri = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        effect = kwargs.get(ATTR_EFFECT)
        if effect not in self._attr_effect_list:
            effect = None
        if not (rgb or kelvin or effect or was_on) and (last := self._last_on):
            # plain "on" brings the lamp back at full white; restore what HA last saw instead
            effect = (
                all_scenes.get(last.scene) if last.scene is not None else PRESET_BY_PAIR.get((last.cold, last.warm))
            )
            if effect is None:
                kelvin, rgb = last.kelvin, None if last.kelvin else last.rgb
            bri = bri or last.brightness
        if effect:  # scene snapshots carry colour attributes too; the effect is what matters
            rgb = kelvin = None
        try:
            if not was_on:
                await self._lamp.send(ilink.ON)
            bri = await self._apply(bri, rgb, kelvin, effect)
        except Exception as err:
            raise self._error(err) from err
        self._attr_is_on = True
        if effect or rgb or kelvin:
            self._attr_effect = effect
        if bri:
            self._attr_brightness = bri
        if rgb:
            self._attr_color_mode, self._attr_rgb_color = ColorMode.RGB, tuple(rgb)
        elif kelvin or effect in PRESETS:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_color_temp_kelvin = kelvin or self._attr_color_temp_kelvin
        elif effect:
            self._attr_color_mode = ColorMode.RGB
        self.async_write_ha_state()
        self._schedule_poll()  # confirm with the lamp's settled state

    def _error(self, err: Exception) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"address": self._address, "error": str(err) or type(err).__name__},
        )

    async def _apply(
        self, bri: int | None, rgb: tuple[int, int, int] | None, kelvin: int | None, effect: str | None
    ) -> int | None:
        # Lamp quirks (measured): any command cancels a fade still in progress, so brightness
        # straight after a colour leaves the LEDs at 0 (the old "colour turns the light off" bug);
        # RGB also resets brightness to 100%. Hence brightness goes *before* CCT/presets and after an RGB fade.
        if effect in SCENE_IDS:
            await self._lamp.send(ilink.scene(SCENE_IDS[effect]))
        elif rgb:
            await self._lamp.send(ilink.rgb(*rgb))
            bri = bri or self.brightness or 255
            if bri != 255:
                await asyncio.sleep(COLOR_FADE)
                await self._lamp.send(ilink.brightness(bri))
        else:
            if effect in PRESETS:  # like the remote's MODE button: modes are at full brightness
                bri = NIGHT_BRI if effect == NIGHT else bri or 255
            frames = [ilink.brightness(bri)] if bri else []
            if effect:
                frames.append(ilink.preset(PRESETS[effect][0]))
            elif kelvin:
                frames.append(ilink.kelvin(kelvin))
            await self._lamp.send(*frames)
        return bri

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._lamp.send(ilink.OFF)
        except Exception as err:
            raise self._error(err) from err
        self._attr_is_on = False
        self.async_write_ha_state()
        self._schedule_poll()
