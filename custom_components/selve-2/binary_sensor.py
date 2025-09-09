from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DATA_API, DATA_SERVER_INFO
from .server import CommeoReceiverState, SeleveHomeServer


FLAG_TO_DEVICE_CLASS: dict[str, BinarySensorDeviceClass | None] = {
    "timeout": BinarySensorDeviceClass.PROBLEM,
    "overload": BinarySensorDeviceClass.PROBLEM,
    "obstacle": BinarySensorDeviceClass.PROBLEM,
    "emergency_alarm": BinarySensorDeviceClass.SAFETY,
    "sensor_learned": None,
    "sensor_connected": BinarySensorDeviceClass.CONNECTIVITY,
    "automatic_mode": None,
    "cc_timeout": None,
    "wind_alarm": BinarySensorDeviceClass.SAFETY,
    "rain_alarm": BinarySensorDeviceClass.SAFETY,
    "frost_alarm": BinarySensorDeviceClass.COLD,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    api: SeleveHomeServer = data[DATA_API]
    coordinator = data["coordinator"]
    server_info = data[DATA_SERVER_INFO]
    parent_identifier = (DOMAIN, f"server_{server_info.mac}")

    entities: list[BinarySensorEntity] = []
    for sid, dev in coordinator.data.items():
        if not isinstance(dev, CommeoReceiverState):
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
                    device_name=dev.name or f"Receiver {sid}",
                )
            )

    async_add_entities(entities)


class SelveFlagBinarySensor(CoordinatorEntity, BinarySensorEntity):
    def __init__(
        self,
        api: SeleveHomeServer,
        coordinator,
        sid: str,
        flag: str,
        parent_identifier,
        device_class: BinarySensorDeviceClass | None,
        device_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.api = api
        self._sid = sid
        self._flag = flag
        self._attr_unique_id = f"{DOMAIN}_{sid}_flag_{flag}"
        self._attr_name = f"{device_name} {flag.replace('_', ' ').title()}"
        self._attr_device_class = device_class
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sid)},
            "via_device": parent_identifier,
        }

    @property
    def is_on(self) -> bool | None:
        dev = self.coordinator.data.get(self._sid)
        if isinstance(dev, CommeoReceiverState):
            attrs = (dev.state or {}).get("attributes") or {}
            val = attrs.get(self._flag)
            if val is None:
                return None
            return bool(val)
        return None

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
