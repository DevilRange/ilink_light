"""Config flow: pick up lamps from Bluetooth discovery or a list of seen lamps."""

from __future__ import annotations

from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
import voluptuous as vol

from .const import DOMAIN
from .ilink import SERVICE_UUID


class IlinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for iLink lamps."""

    VERSION = 3

    _info: BluetoothServiceInfoBleak
    _seen: dict[str, str]

    async def async_step_bluetooth(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(info.address)
        self._abort_if_unique_id_configured()
        self._info = info
        self.context["title_placeholders"] = {"name": info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="iLink Light", data={CONF_ADDRESS: self._info.address})
        self._set_confirm_only()
        return self.async_show_form(step_id="confirm", description_placeholders={"name": self._info.name})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ADDRESS])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="iLink Light", data=user_input)
        current = self._async_current_ids()
        self._seen = {
            i.address: f"{i.name} ({i.address})"
            for i in async_discovered_service_info(self.hass)
            if SERVICE_UUID in i.service_uuids and i.address not in current
        }
        if not self._seen:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._seen)})
        )
