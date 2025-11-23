from __future__ import annotations
from .server import SelveState
from typing import Any, cast

from homeassistant import core
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta
from time import monotonic
import logging
import contextlib
import asyncio
import json
import socket
import struct

from .const import (
    DOMAIN,
    DATA_API,
    DATA_SERVER_INFO,
    DATA_DEVICES,
    CONF_NAME,
    CONF_DISABLE_POLLING,
    DEFAULT_UPDATE_INTERVAL,
    PLATFORMS,
    MULTICAST_GROUP,
    MULTICAST_PORT,
)

from .server import (
    ETYPE_LABELS,
    SeleveHomeServer,
    DataStoreDict,
    SelveRawCommeoDeviceState,
    SelveStates,
    parseCommeoRawFlags,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(_hass: core.HomeAssistant, _config: ConfigType) -> bool:
    """Set up integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Selve Home Server 2 from a config entry."""
    host: str = cast(str, entry.data[CONF_HOST])
    password: str = cast(str, entry.data[CONF_PASSWORD])
    custom_server_name: str | None = entry.data.get(CONF_NAME)

    disable_polling: bool = cast(bool, entry.options.get(CONF_DISABLE_POLLING, False))
    api = SeleveHomeServer(host, password)
    server_info = await hass.async_add_executor_job(api.get_server_info)
    states = await hass.async_add_executor_job(api.get_states)
    if states is None:
        raise UpdateFailed("No data received from Selve Home Server")

    async def async_update_data() -> SelveStates:
        try:
            new_states = await hass.async_add_executor_job(api.get_states)
            if new_states is None:
                raise UpdateFailed("No data received from Selve Home Server")
        except Exception as err:
            raise UpdateFailed(str(err)) from err

        udp_last = store.get("udp_last", {})
        now = monotonic()
        for sid, dev in new_states.items():
            if "state" not in dev or not isinstance(dev["state"], dict):
                continue
            udp_entry = udp_last.get(sid)
            if not udp_entry:
                continue
            udp_state = udp_entry.get("state", {})
            ts = udp_entry.get("ts", 0)
            for k, udp_val in udp_state.items():
                api_val = dev["state"].get(k)
                if api_val != udp_val:
                    # If UDP is recent, silently prefer it; otherwise, warn about mismatch
                    if now - ts <= 20:
                        dev["state"][k] = udp_val
                    else:
                        _LOGGER.warning(
                            "Selve API mismatch for %s.%s: poll=%s udp=%s",
                            sid,
                            k,
                            api_val,
                            udp_val,
                        )
        return new_states

    coordinator: DataUpdateCoordinator[SelveStates]

    if disable_polling:

        async def _return_states() -> SelveStates:
            return states

        coordinator = DataUpdateCoordinator[SelveStates](
            hass,
            _LOGGER,
            name=f"Selve Home Server {host}",
            update_method=_return_states,
            update_interval=None,
        )
    else:
        coordinator = DataUpdateCoordinator[SelveStates](
            hass,
            _LOGGER,
            name=f"Selve Home Server {host}",
            update_method=async_update_data,
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
    coordinator.logger.debug("Polling is enabled: %s", not disable_polling)
    store: DataStoreDict = {
        DATA_API: api,
        DATA_SERVER_INFO: server_info,
        DATA_DEVICES: states,
        "udp_last": {},
        "coordinator": coordinator,
        "udp_task": None,
    }

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = store

    await coordinator.async_config_entry_first_refresh()

    dev_reg = dr.async_get(hass)
    # Create main server device
    _ = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"server_{server_info['mac']}")},
        manufacturer="Selve",
        name=custom_server_name or f"Selve Home Server ({host})",
        model=f"Home Server 2 ({server_info['mhv']})",
        sw_version=server_info["mhv"],
        entry_type=DeviceEntryType.SERVICE,
    )

    for sid, device in states.items():
        # Determine model based on device class
        if device["type"] == "CM":
            if device["deviceType"] == "00":
                model = f"Commeo {ETYPE_LABELS[device['eType']]}".strip()
            else:
                model = "Commeo Sensor"
        elif device.__class__.__name__ == "IveoReceiverState":
            model = "Iveo Receiver"
        elif device.__class__.__name__ == "DeviceGroupState":
            model = "Device Group"
        else:
            model = "Unknown"

        _ = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{sid}")},
            manufacturer="Selve",
            name=device.get("name") or f"Device {sid}",
            model=model,
            via_device=(DOMAIN, f"server_{server_info['mac']}"),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _udp_listener():
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        sock.bind(("0.0.0.0", MULTICAST_PORT))
        mreq = struct.pack("=4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        while True:
            _LOGGER.warning("UDP wait")
            try:
                data, addr = await loop.sock_recvfrom(sock, 65535)  # pyright: ignore[reportAny]
            except asyncio.CancelledError as err:
                _LOGGER.error("UDP listener cancelled")
                raise err
            except Exception as err:
                _LOGGER.error("UDP recv error: %s", err)
                await asyncio.sleep(0.1)
                continue

            msg = data.decode("utf-8", errors="replace").strip()
            if not (msg.startswith("STA:") or msg.startswith("EVT:")):
                _LOGGER.warning(
                    "Selve UDP: unexpected prefix: addr=%s msg=%s",
                    addr,  # pyright: ignore[reportAny]
                    msg,
                )
                continue
            _LOGGER.error("Selve UDP: %s", msg)
            payload = msg[4:]
            try:
                j = cast(dict[str, Any], json.loads(payload))  # pyright: ignore[reportExplicitAny]
            except Exception as err:
                _LOGGER.warning("Selve UDP: invalid JSON: %s (%s)", payload[:200], err)
                continue

            sid = cast(str, j.get("sid"))
            if not sid:
                continue
            dev: SelveState | None = coordinator.data.get(sid)
            if not dev:
                continue

            changed_values: list[str] | None = j.get("changed") or None
            udp_state = j.get("state") or {}
            if changed_values is not None and len(changed_values) == 7:
                if "state" not in dev:
                    continue
                dev_state = dev["state"]
                if not isinstance(dev_state, dict):
                    continue
                last_run_state = dev_state.get("run_state")
                if last_run_state == 0:  # didn't move last update
                    all_values = [
                        "overload",
                        "obstacle",
                        "alarm",
                        "position",
                        "current",
                        "target",
                        "running_state",
                    ]
                    if set(changed_values) == set(
                        all_values
                    ):  # this is likely a wrong state
                        run_state = cast(int | None, udp_state.get("run_state", None))
                        position = cast(int | None, udp_state.get("position", None))
                        current = cast(int | None, udp_state.get("current", None))
                        target = cast(int | None, udp_state.get("target", None))
                        timeout = cast(int | None, udp_state.get("timeout", None))
                        if (
                            run_state == 0
                            and position == 0
                            and current == 100
                            and target == 100
                            and timeout == 0
                        ):
                            _LOGGER.warning(
                                "Selve UDP: ignoring likely wrong state for %s: %s",
                                sid,
                                udp_state,
                            )
                            continue

            # Normalize flags -> attributes for Commeo receivers so binary_sensors update from UDP
            if dev["type"] == "CM":
                try:
                    parsed_falgs = parseCommeoRawFlags(
                        cast(SelveRawCommeoDeviceState, udp_state)
                    )
                    udp_state["parsed_flags"] = parsed_falgs
                except Exception:
                    coordinator.logger.error(
                        "Error parsing Commeo flags from UDP for device %s udp_state %s",
                        dev,
                        udp_state,
                    )
            if "state" in dev and isinstance(dev["state"], dict):
                dev["state"].update(udp_state)
                coordinator.data[sid] = dev
                store["udp_last"][sid] = {"state": udp_state, "ts": monotonic()}
                # Notify entities
                coordinator.async_set_updated_data(coordinator.data)

    task = hass.loop.create_task(_udp_listener())
    store["udp_task"] = task
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_store = cast(dict[str, DataStoreDict], hass.data.get(DOMAIN, {})).get(
        entry.entry_id
    )
    # Cancel UDP listener
    udp_task = entry_store.get("udp_task") if entry_store else None
    if udp_task:
        _ = udp_task.cancel()
        with contextlib.suppress(Exception):
            await udp_task

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_store = cast(dict[str, object], hass.data.get(DOMAIN, {}))
        _ = domain_store.pop(entry.entry_id, None)
    return unload_ok
