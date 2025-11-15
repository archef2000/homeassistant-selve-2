from functools import cached_property
from typing import cast, override

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
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


FLAG_TO_DEVICE_CLASS: dict[str, BinarySensorDeviceClass | None] = {
    "timeout": BinarySensorDeviceClass.PROBLEM,
    "overload": BinarySensorDeviceClass.PROBLEM,
    "obstacle": BinarySensorDeviceClass.PROBLEM,
    "emergency_alarm": BinarySensorDeviceClass.SAFETY,
    "sensor_learned": None,
    "sensor_connected": BinarySensorDeviceClass.CONNECTIVITY,
    "cc_timeout": None,
    "wind_alarm": BinarySensorDeviceClass.SAFETY,
    "rain_alarm": BinarySensorDeviceClass.SAFETY,
    "frost_alarm": BinarySensorDeviceClass.COLD,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
):
    data = cast(DataStoreDict, hass.data[DOMAIN][entry.entry_id])
    api: SeleveHomeServer = data[DATA_API]
    coordinator = data["coordinator"]
    server_info = data[DATA_SERVER_INFO]
    parent_identifier = (DOMAIN, f"server_{server_info['mac']}")

    entities: list[BinarySensorEntity] = []
    for sid, dev in coordinator.data.items():
        if dev["type"] != "CM":
            continue
        for flag_name in FLAG_TO_DEVICE_CLASS.keys():
            entities.append(
                SelveFlagBinarySensor(
                    api,
                    coordinator,
                    sid,
                    flag_name,
                    parent_identifier,
                    device_class=FLAG_TO_DEVICE_CLASS[flag_name],
                    device_name=dev["name"] or f"Receiver {sid}",
                )
            )

    async_add_entities(entities)


class SelveFlagBinarySensor(BinarySensorEntity):
    def __init__(
        self,
        api: SeleveHomeServer,
        coordinator: DataUpdateCoordinator[SelveStates],
        sid: str,
        flag: str,
        parent_identifier: tuple[str, str],
        device_class: BinarySensorDeviceClass | None,
        device_name: str,
    ) -> None:
        self.coordinator: DataUpdateCoordinator[SelveStates] = coordinator
        self.api: SeleveHomeServer = api
        self._sid: str = sid
        self._flag: str = flag
        self._attr_unique_id: str | None = f"{DOMAIN}_{sid}_flag_{flag}"
        self._attr_name: str | None = f"{device_name} {flag.replace('_', ' ').title()}"
        self._attr_device_class: BinarySensorDeviceClass | None = device_class
        self._attr_device_info: DeviceInfo | None = {
            "identifiers": {(DOMAIN, sid)},
            "via_device": parent_identifier,
        }

    @cached_property
    @override
    def is_on(self) -> bool | None:
        dev = cast(SelveCommeoState | None, self.coordinator.data.get(self._sid))
        if dev is None or dev.get("type") != "CM":
            return None
        flags = dev["state"].get("parsed_flags")
        if not flags:
            return None
        val = flags.get(self._flag)
        if val is None:
            return None
        return cast(bool, val)

    @override
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    def _handle_coordinator_update(self) -> None:
        self.__dict__.pop("is_on", None)
        self.async_write_ha_state()
