"""Logbook entries for mode, brightness and colour changes (attributes don't show up on their own)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_ENTITY_ID, LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_CHANGED


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event: Callable) -> None:
    """Describe iLink logbook events."""

    @callback
    def describe(event: Event) -> dict[str, str]:
        entity_id = event.data["entity_id"]
        state = hass.states.get(entity_id)
        suffix = " (remote)" if event.data.get("remote") else ""
        return {
            LOGBOOK_ENTRY_NAME: state.name if state else entity_id,
            LOGBOOK_ENTRY_MESSAGE: f"{event.data['change']}{suffix}",
            LOGBOOK_ENTRY_ENTITY_ID: entity_id,
        }

    async_describe_event(DOMAIN, EVENT_CHANGED, describe)
