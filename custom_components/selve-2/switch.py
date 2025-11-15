from __future__ import annotations

from functools import cached_property
from typing import cast, override

from requests import Response

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, DATA_API, DATA_SERVER_INFO
from .server import (
    DataStoreDict,
    SeleveHomeServer,
    SelveCommeoState,
    SelveStates,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = cast(DataStoreDict, hass.data[DOMAIN][entry.entry_id])
    api: SeleveHomeServer = data[DATA_API]
    coordinator = data["coordinator"]
    server_info = data[DATA_SERVER_INFO]
    parent_identifier: tuple[str, str] = (DOMAIN, f"server_{server_info['mac']}")

    entities: list[SwitchEntity] = []
    for dev in coordinator.data.values():
        if dev["type"] != "CM":
            continue
        if dev["deviceType"] != "00":
            continue
        entities.append(
            SelveAutomaticModeSwitch(api, coordinator, dev, parent_identifier)
        )

    async_add_entities(entities)


class SelveAutomaticModeSwitch(SwitchEntity):
    def __init__(
        self,
        api: SeleveHomeServer,
        coordinator: DataUpdateCoordinator[SelveStates],
        dev: SelveCommeoState,
        parent_identifier: tuple[str, str],
    ) -> None:
        self.api: SeleveHomeServer = api
        self.coordinator: DataUpdateCoordinator[SelveStates] = coordinator
        self._sid: str = dev["sid"]
        self._attr_device_class: SwitchDeviceClass | None = SwitchDeviceClass.SWITCH
        self._attr_unique_id: str | None = f"{DOMAIN}_{self._sid}_automatic_mode"
        device_name = dev.get("name") or f"Receiver {self._sid}"
        self._attr_name: str | None = f"{device_name} Automatic Mode"
        self._attr_device_info: DeviceInfo | None = DeviceInfo(
            identifiers={(DOMAIN, self._sid)},
            via_device=parent_identifier,
        )

    @override
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    def _handle_coordinator_update(self) -> None:
        self.__dict__.pop("is_on", None)
        self.async_write_ha_state()

    @cached_property
    @override
    def is_on(self) -> bool | None:
        dev = cast(SelveCommeoState | None, self.coordinator.data.get(self._sid))
        if dev is None:
            return None
        flags = dev["state"].get("parsed_flags")
        if not flags:
            return None
        if "automatic_mode" not in flags:
            return None
        return bool(flags["automatic_mode"])

    @override
    async def async_turn_on(self, **_kwargs: object) -> None:
        _response: Response = await self.hass.async_add_executor_job(
            self.api.send_command,
            self._sid,
            "auto",
        )

    @override
    async def async_turn_off(self, **_kwargs: object) -> None:
        _response: Response = await self.hass.async_add_executor_job(
            self.api.send_command,
            self._sid,
            "manu",
        )
