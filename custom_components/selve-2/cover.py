from functools import cached_property
from typing import Any, Literal, cast, override
from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DATA_API, DATA_SERVER_INFO
from .server import (
    DataStoreDict,
    SeleveHomeServer,
    SelveCommeoState,
    SelveIveoState,
    SelveStates,
)

# Motor related eTypes (0..7 plus ROLLER_SHUTTER etc.)
MOTOR_ETYPES = {0, 1, 2, 3, 4, 5, 6, 7}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    data = cast(DataStoreDict, hass.data[DOMAIN][entry.entry_id])
    api: SeleveHomeServer = data[DATA_API]
    coordinator = data["coordinator"]
    server_info = data[DATA_SERVER_INFO]
    parent_identifier: tuple[Literal["selve"], str] = (
        DOMAIN,
        f"server_{server_info['mac']}",
    )
    states = coordinator.data

    entities: list[CoverEntity] = []
    for dev in states.values():
        if dev["type"] == "CM" and dev["deviceType"] == "00":
            eTypeCode = dev["eType"]
            if eTypeCode in MOTOR_ETYPES:
                entities.append(
                    SelveCover(api, coordinator, entry.entry_id, dev, parent_identifier)
                )
        elif dev["type"] == "IV":
            entities.append(
                SelveCover(api, coordinator, entry.entry_id, dev, parent_identifier)
            )
    async_add_entities(entities)


class SelveCover(CoverEntity):
    """Cover entity for Commeo motor receivers (with position)."""

    def __init__(
        self,
        api: SeleveHomeServer,
        coordinator: DataUpdateCoordinator[SelveStates],
        entry_id: str,
        dev: SelveCommeoState | SelveIveoState,
        parent_identifier: tuple[str, str],
    ):
        self.coordinator: DataUpdateCoordinator[SelveStates] = coordinator
        self.api: SeleveHomeServer = api
        self._dev: SelveCommeoState | SelveIveoState = dev
        self._attr_unique_id: str | None = f"{DOMAIN}_{dev['sid']}"
        self._attr_name: str | None = dev.get("name") or f"Receiver {dev['sid']}"
        self._attr_device_info: DeviceInfo | None = {
            "identifiers": {(DOMAIN, dev["sid"])},
            "via_device": parent_identifier,
        }
        if dev["type"] == "CM":
            self._attr_supported_features: CoverEntityFeature | None = (  # pyright: ignore[reportRedeclaration]
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
                | CoverEntityFeature.SET_POSITION
            )
        elif dev["type"] == "IV":
            self._attr_supported_features: CoverEntityFeature | None = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
            )

    @cached_property
    def current_cover_position(self) -> int | None:
        if self._dev["type"] != "CM":
            return None
        if self._dev["state"]["position"] == "-":
            return None
        return 100 - int(self._dev["state"]["position"])

    @cached_property
    def is_closed(self) -> bool | None:
        if self._dev["type"] == "IV":
            state = self._dev["state"]
            return state == "closed"
        pos = self.current_cover_position
        if pos is None:
            return None
        # Assume 0 = fully closed
        return pos == 0

    @override
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    def _handle_coordinator_update(self) -> None:
        updated = self.coordinator.data.get(self._dev["sid"])
        if updated is not None:
            self._dev = cast(SelveCommeoState | SelveIveoState, updated)
        self.__dict__.pop("current_cover_position", None)
        self.__dict__.pop("is_closed", None)
        self.__dict__.pop("extra_state_attributes", None)
        self.async_write_ha_state()

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:  # pyright: ignore[reportAny, reportExplicitAny]
        _ = await self.hass.async_add_executor_job(
            self.api.send_command, self._dev["sid"], "moveUp"
        )

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:  # pyright: ignore[reportAny, reportExplicitAny]
        _ = await self.hass.async_add_executor_job(
            self.api.send_command, self._dev["sid"], "moveDown"
        )

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:  # pyright: ignore[reportExplicitAny, reportAny]
        _ = await self.hass.async_add_executor_job(
            self.api.send_command, self._dev["sid"], "stop"
        )

    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:  # pyright: ignore[reportExplicitAny, reportAny]
        raw_position = cast(int, kwargs.get("position", 0))
        pos = 100 - raw_position
        _ = await self.hass.async_add_executor_job(
            self.api.send_command, self._dev["sid"], "moveTo", pos
        )

    @cached_property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:  # pyright: ignore[reportExplicitAny]
        attributes: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]
        attributes["sid"] = self._dev["sid"]
        attributes["adr"] = self._dev["adr"]
        if self._dev["type"] == "CM":
            attrs = self._dev["state"]
            attributes["timeout"] = bool(attrs["timeout"])
            attributes["flags"] = self._dev["state"]["flags"]
        return attributes
