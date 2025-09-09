from __future__ import annotations

from typing import Any
from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DATA_API, DATA_SERVER_INFO
from .server import CommeoReceiverState, IveoReceiverState, SeleveHomeServer

# Motor related eTypes (0..7 plus ROLLER_SHUTTER etc.)
MOTOR_ETYPES = {0, 1, 2, 3, 4, 5, 6, 7}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    api: SeleveHomeServer = data[DATA_API]
    coordinator = data["coordinator"]
    server_info = data[DATA_SERVER_INFO]
    parent_identifier = (DOMAIN, f"server_{server_info.mac}")
    states = coordinator.data

    entities: list[CoverEntity] = []
    for sid, dev in states.items():
        if isinstance(dev, CommeoReceiverState):
            eType_code = getattr(dev, "eType_code", None)
            if isinstance(eType_code, int) and eType_code in MOTOR_ETYPES:
                entities.append(SelveCover(
                    api, coordinator, entry.entry_id, dev, parent_identifier))
        elif isinstance(dev, IveoReceiverState):
            entities.append(SelveIveoCover(api, coordinator,
                            entry.entry_id, dev, parent_identifier))
    async_add_entities(entities)


class SelveCover(CoordinatorEntity, CoverEntity):
    """Cover entity for Commeo motor receivers (with position)."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, api: SeleveHomeServer,
                 coordinator, entry_id: str,
                 dev: CommeoReceiverState, parent_identifier):
        super().__init__(coordinator)
        self.api = api
        self._dev = dev
        self._attr_unique_id = f"{DOMAIN}_{dev.sid}"
        self._attr_name = dev.name or f"Receiver {dev.sid}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, dev.sid)},
            "via_device": parent_identifier,
        }

    @property
    def current_cover_position(self) -> int | None:
        return 100 - int(self._dev.state["position"])

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        # Assume 0 = fully closed
        return pos == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "moveUp")
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "moveDown")
        await self.coordinator.async_request_refresh()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "stop")
        await self.coordinator.async_request_refresh()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        pos = 100 - kwargs.get("position", 0)
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "moveTo", pos)
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        # Update local device reference after coordinator refresh
        new_dev = self.coordinator.data.get(self._dev.sid)
        if isinstance(new_dev, CommeoReceiverState):
            self._dev = new_dev
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = self._dev.state.get("attributes") or {}
        attributes = {}
        attributes["timeout"] = bool(attrs["timeout"])
        attributes["flags"] = self._dev.state["flags"]
        return attributes


class SelveIveoCover(CoordinatorEntity, CoverEntity):
    """Cover entity for Iveo receivers (basic up/down/stop)."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, api: SeleveHomeServer,
                 coordinator, entry_id: str,
                 dev: IveoReceiverState, parent_identifier):
        super().__init__(coordinator)
        self.api = api
        self._dev = dev
        self._attr_unique_id = f"{DOMAIN}_iv_{dev.sid}"
        self._attr_name = f"Iveo Receiver {dev.sid}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, dev.sid)},
            "via_device": parent_identifier,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "moveUp")
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "moveDown")
        await self.coordinator.async_request_refresh()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.api.send_command, self._dev.sid, "stop")
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        # Replace device reference if updated
        new_dev = self.coordinator.data.get(self._dev.sid)
        if isinstance(new_dev, IveoReceiverState):
            self._dev = new_dev
        self.async_write_ha_state()
