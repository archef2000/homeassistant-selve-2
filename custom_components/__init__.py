from __future__ import annotations

from homeassistant import core
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
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
    DEFAULT_UPDATE_INTERVAL,
    PLATFORMS,
    MULTICAST_GROUP,
    MULTICAST_PORT,
)

from .server import (
    SeleveHomeServer,
    CommeoReceiverState,
    CommeoSensorState,
    IveoReceiverState,
    DeviceGroupState,
)

_LOGGER = logging.getLogger(__name__)

DeviceState = (
    CommeoReceiverState | CommeoSensorState | IveoReceiverState | DeviceGroupState
)


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Selve Home Server 2 from a config entry."""
    host: str = entry.data[CONF_HOST]
    password: str = entry.data[CONF_PASSWORD]
    custom_server_name: str | None = entry.data.get(CONF_NAME)

    api = SeleveHomeServer(host, password)
    # Fetch server info synchronously in executor (requests library is blocking)
    server_info = await hass.async_add_executor_job(api.get_server_info)
    states = await hass.async_add_executor_job(api.get_states)

    # Prepare store early so update method can access udp_last during first refresh
    store: dict = {
        DATA_API: api,
        DATA_SERVER_INFO: server_info,
        DATA_DEVICES: states,
        "udp_last": {},
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = store

    async def async_update_data() -> dict[str, DeviceState]:
        try:
            new_states = await hass.async_add_executor_job(api.get_states)
        except Exception as err:
            raise UpdateFailed(str(err)) from err

        # Compare API state vs last UDP state and prefer recent UDP for UI
        udp_last: dict = store.get("udp_last", {})
        now = monotonic()
        for sid, dev in new_states.items():
            if not hasattr(dev, "state") or not isinstance(dev.state, dict):
                continue
            udp_entry = udp_last.get(sid)
            if not udp_entry:
                continue
            udp_state = udp_entry.get("state", {})
            ts = udp_entry.get("ts", 0)
            for k, udp_val in udp_state.items():
                api_val = dev.state.get(k)
                if api_val != udp_val:
                    # If UDP is recent, silently prefer it; otherwise, warn about mismatch
                    if now - ts <= 20:
                        dev.state[k] = udp_val
                    else:
                        _LOGGER.warning(
                            "Selve API mismatch for %s.%s: poll=%s udp=%s",
                            sid,
                            k,
                            api_val,
                            udp_val,
                        )
        return new_states

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Selve Home Server {host}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    store["coordinator"] = coordinator

    dev_reg = dr.async_get(hass)
    # Create main server device
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"server_{server_info.mac}")},
        manufacturer="Selve",
        name=custom_server_name or f"Selve Home Server ({host})",
        model=f"Home Server 2 ({server_info.mhv})",
        sw_version=server_info.mfv,
        entry_type=DeviceEntryType.SERVICE,
    )

    for sid, device in states.items():
        # Determine model based on device class
        if device.__class__.__name__ == "CommeoReceiverState":
            model = f"Commeo {device.eType}".strip()
        elif device.__class__.__name__ == "CommeoSensorState":
            model = "Commeo Sensor"
        elif device.__class__.__name__ == "IveoReceiverState":
            model = "Iveo Receiver"
        elif device.__class__.__name__ == "DeviceGroupState":
            model = "Device Group"
        else:
            model = "Unknown"

        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{sid}")},
            manufacturer="Selve",
            name=device.name,
            model=model,
            via_device=(DOMAIN, f"server_{server_info.mac}"),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _udp_listener():
        loop = asyncio.get_running_loop()
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        sock.bind(("0.0.0.0", MULTICAST_PORT))
        mreq = struct.pack("=4sl", socket.inet_aton(
            MULTICAST_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        while True:
            try:
                data, addr = await loop.sock_recvfrom(sock, 65535)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("UDP recv error: %s", err)
                await asyncio.sleep(0.1)
                continue

            msg = data.decode("utf-8", errors="replace").strip()
            if not (msg.startswith("STA:") or msg.startswith("EVT:")):
                _LOGGER.warning("Selve UDP: unexpected prefix: %s", msg[:16])
                continue
            payload = msg[4:]
            try:
                j = json.loads(payload)
            except Exception as err:
                _LOGGER.warning(
                    "Selve UDP: invalid JSON: %s (%s)", payload[:200], err)
                continue

            sid = j.get("sid")
            if not sid:
                continue
            dev = coordinator.data.get(sid)
            if not dev:
                continue

            udp_state = j.get("state") or {}
            # Normalize flags -> attributes for Commeo receivers so binary_sensors update from UDP
            if isinstance(dev, CommeoReceiverState) and "flags" in udp_state:
                try:
                    raw = udp_state.get("flags")
                    flags_int = int(raw, 10) if isinstance(
                        raw, str) else int(raw)
                    attrs = CommeoReceiverState._parse_flags(flags_int)
                    # Merge into attributes map
                    existing_attrs = (dev.state.get("attributes") if isinstance(
                        dev.state, dict) else {}) or {}
                    merged = {**existing_attrs, **attrs}
                    udp_state["attributes"] = merged
                    # Remove raw flags field after parsing
                    udp_state.pop("flags", None)
                except Exception:
                    pass
            if hasattr(dev, "state") and isinstance(dev.state, dict):
                dev.state.update(udp_state)
                coordinator.data[sid] = dev
                store["udp_last"][sid] = {
                    "state": udp_state, "ts": monotonic()}
                # Notify entities
                coordinator.async_set_updated_data(coordinator.data)

    task = hass.loop.create_task(_udp_listener())
    store["udp_task"] = task
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    # Cancel UDP listener
    udp_task = entry_store.get("udp_task") if entry_store else None
    if udp_task:
        udp_task.cancel()
        with contextlib.suppress(Exception):
            await udp_task

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
