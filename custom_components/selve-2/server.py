from asyncio import Task
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import requests
from typing import Any, Literal, TypeAlias, TypedDict, cast
from enum import IntEnum
import base64
import logging
from ftfy import fix_encoding


_LOGGER = logging.getLogger(__name__)


def fix_mojibake(name: str) -> str:
    """Normalize encoding issues using ftfy.

    Converts mojibake like 'GÃ¤stezimmer' to 'Gästezimmer'.
    """
    if not name:
        return name
    # fix_encoding handles double-encoding; strip preserves user spacing
    return fix_encoding(name).strip()


class CommeoEType(IntEnum):
    """Numeric Commeo receiver/switch/motor type codes."""

    BLIND_INSIDE = 0
    BLIND_OUTSIDE = 1
    AWNING_INSIDE = 2
    AWNING_OUTSIDE = 3
    AWNING_BUSINESS = 4
    ROLLER_SHUTTER = 5
    WINDOW = 6
    FOLDING_SHUTTER = 7
    # 8..15: Unknown Motor type
    LIGHT_NIGHT = 16
    LIGHT_DUSK = 17
    HEATING = 18
    COOLING = 19
    SWITCH = 20
    SWITCH_SUN = 21
    # 22..31: Unknown Switch type


ETYPE_LABELS: dict[int, str] = {
    0: "Inside Blind",
    1: "Outside Blind",
    2: "Inside Awning",
    3: "Outside Awning",
    4: "Business Awning",
    5: "Roller Shutter",
    6: "Window",
    7: "Folding Shutter",
    16: "Night Light",
    17: "Dusk Light",
    18: "Heating",
    19: "Cooling",
    20: "Switch",
    21: "Sun Switch",
}


def label_for_e_type(code: int | None) -> str | None:
    if code is None:
        return None
    if code in ETYPE_LABELS:
        return ETYPE_LABELS[code]
    if 8 <= code <= 15:
        return "Unknown Motor Type"
    if 22 <= code <= 31:
        return "Unknown Switch Type"
    return "Unknown"


class ServerInfo(TypedDict):
    """Represents the server information returned by the SELVE-Home system."""

    name: str
    """Name of the SELVE-Home system"""

    mhv: str
    """Hardware model of the device"""

    mfv: str
    """Firmware version installed on the device"""

    msv: str
    """Current Firmware Version running on the system"""

    hwv: str
    """Hardware version of the device"""

    vid: str
    """Vendor ID of the device"""

    mem: int
    """Available memory in bytes"""

    ip: str
    """IP Address of the device on the local network"""

    sn: str
    """Subnet mask of the network configuration"""

    gw: str
    """Gateway address for network routing"""

    dns: str
    """DNS Server address used for domain resolution"""

    mac: str
    """MAC Address of the device (hardware identifier)"""

    ntp: str
    """Time Server address used for synchronizing device time"""

    start: int
    """System start time in Unix timestamp format"""

    time: int
    """Current system time in Unix timestamp format"""

    loc: str
    """Location identifier of the device"""

    serial: str
    """Serial communication settings (baud rate, parity, stop bits)"""

    io: str
    """IO configuration of the device"""

    cfg: str
    """Configuration status of the device"""

    server: str
    """Connected Cloud Server address and port"""

    sid: str
    """System ID in the cloud. Changes if server is being reset"""

    locked: bool
    """Indicates whether the device is in a locked state"""

    wifi: str
    """Name of the connected WiFi network"""

    rssi: int
    """WiFi signal strength in dBm (negative number, closer to 0 is stronger)"""


CammeoSwitchCommands = [
    "on",
    "off",
    "toggle",
]

CammeoMotorCommands = [
    "moveUp",
    "moveDown",
    "stop",
    "moveTo",  # with value
    "stepUp",
    "stepDown",
    "movetoP1",
    "movetoP2",
    "auto",
    "manu",
    "saveP1",
    "saveP2",
]

IveoSwitchCommands = ["on", "off"]

IveoMotorCommands = [
    "moveUp",
    "moveDown",
    "stop",
    "moveToP1",
    "moveToP2",
    "saveP1",
    "saveP2",
    "delete",
    "configP1",
    "configP2",
]


class SelveCommeoDeviceFlags(TypedDict):
    timeout: bool
    overload: bool
    obstacle: bool
    emergency_alarm: bool
    sensor_learned: bool
    sensor_connected: bool
    automatic_mode: bool
    cc_timeout: bool
    wind_alarm: bool
    rain_alarm: bool
    frost_alarm: bool


class SelveRawCommeoDeviceState(TypedDict):
    position: int | Literal["-"]
    run_state: int
    current: int
    target: int
    flags: str
    timeout: int
    parsed_flags: SelveCommeoDeviceFlags | None


class SelveRawCommeoState(TypedDict):
    type: Literal["CM"]
    sid: str  # HEX
    """The device id inside the gateway"""
    adr: str  # HEX
    """The RF address of the device"""
    cid: str  # HEX
    """Commeo ID : The id of the device in CC. (not relevent for operations)"""
    deviceType: str
    """0: Receiver 1: Sensor"""
    eType: str
    name: str
    state: SelveRawCommeoDeviceState
    group: str  # HEX
    """Group Sid. Only set if device is part of a group"""


class SelveCommeoState(TypedDict):
    type: Literal["CM"]
    sid: str  # HEX
    """The device id inside the gateway"""
    adr: str  # HEX
    """The RF address of the device"""
    cid: str  # HEX
    """Commeo ID : The id of the device in CC. (not relevent for operations)"""
    deviceType: str
    """0: Receiver 1: Sensor"""
    eType: int
    name: str
    state: SelveRawCommeoDeviceState
    group: str  # HEX
    """Group Sid. Only set if device is part of a group"""


class SelveIveoState(TypedDict):
    type: Literal["IV"]
    sid: str
    adr: str  # HEX
    """The RF address of the device"""
    config: str
    state: Literal["open", "closed"]


class SelveRawDeviceGroupState(TypedDict):
    type: Literal["SGROUP"]
    sid: str  # HEX
    """The device id inside the gateway"""
    adr: str  # HEX
    """The RF address of the device"""
    sys: Literal["CM", "IV"]
    deviceType: str
    name: str
    "in base64"


class SelveRawEventState(TypedDict):
    type: Literal["EVENT"]
    adr: str
    state: str


SelveRawState: TypeAlias = (
    SelveRawCommeoState | SelveIveoState | SelveRawDeviceGroupState | SelveRawEventState
)


SelveRawStates: TypeAlias = list[SelveRawState]

SelveState: TypeAlias = (
    SelveCommeoState | SelveIveoState | SelveRawDeviceGroupState | SelveRawEventState
)


SelveStates: TypeAlias = dict[str, SelveState]


def parseCommeoRawFlags(
    raw_state: SelveRawCommeoDeviceState,
) -> SelveCommeoDeviceFlags | None:
    """Parse the flags from the state data with reversed bit order (endianness).
    Bit mapping:
    Bit 0: Timeout - 0: No time out, 1: timeout
    Bit 1: Overload - 1: overload
    Bit 2: Obstacle - 1: obstacle
    Bit 3: Emergency alarm - 1: emergency alarm
    Bit 4: Sensor learned - 0: no sensor learned, 1: Sensor is learned
    Bit 5: Sensor lost - 0: sensor is connected, 1: sensor is lost
    Bit 6: Mode - 0: manu, 1: automatic
    Bit 7: Timeout - 1: device no longer is in CC
    Bit 8: Wind alarm
    Bit 9: Rain alarm
    Bit 10: Frost alarm
    Bit 11-15: Reserved
    """
    raw_flags = raw_state.get("flags", "-")
    if raw_flags == "-":
        return None
    if len(raw_flags) != 4:
        raise ValueError("Flags string must be 4 hex digits")
    flags = int(raw_flags, 16)
    parsed_flags: SelveCommeoDeviceFlags = {
        "timeout": bool(flags & (1 << 0)),
        "overload": bool(flags & (1 << 1)),
        "obstacle": bool(flags & (1 << 2)),
        "emergency_alarm": bool(flags & (1 << 3)),
        "sensor_learned": bool(flags & (1 << 4)),
        "sensor_connected": not bool(flags & (1 << 5)),
        "automatic_mode": bool(flags & (1 << 6)),
        "cc_timeout": bool(flags & (1 << 7)),
        "wind_alarm": bool(flags & (1 << 8)),
        "rain_alarm": bool(flags & (1 << 9)),
        "frost_alarm": bool(flags & (1 << 10)),
    }
    return parsed_flags


def parseCommeoRawState(raw_state: SelveRawCommeoState) -> SelveCommeoState:
    eType = int(raw_state["eType"])
    state: SelveCommeoState = {
        **raw_state,
        "name": fix_mojibake(raw_state.get("name")),
        "eType": eType,
        "state": {
            **raw_state["state"],
            "parsed_flags": parseCommeoRawFlags(raw_state["state"]),
        },
    }
    return state


class SeleveHomeServer:
    def __init__(self, host: str, password: str):
        if not (host.startswith("https://") or host.startswith("http://")):
            host = f"http://{host}"
        self.host: str = host
        self.password: str = password

    def request(self, method: str, path: str, data: Any | None = None):  # pyright: ignore[reportExplicitAny]
        url = f"{self.host}{path}"
        url += f"?auth={self.password}"
        response = requests.request(method, url, json=data)
        return response

    def get_server_info(self) -> ServerInfo:
        response = self.request("GET", "/info")
        if response.status_code == 200 and "XC_SUC" in response.json():
            data = cast(ServerInfo, response.json()["XC_SUC"])
            return {**data, "name": fix_mojibake(data.get("name", ""))}
        else:
            raise Exception(
                f"Failed to get server info: {response.status_code} {response.text}"
            )

    def request_cmd(self, params: dict[str, str]):
        params["auth"] = self.password
        url = "/cmd?auth=" + self.password
        url += "&" + "&".join(f"{k}={v}" for k, v in params.items())
        response = self.request("GET", url)
        if response.status_code == 200:
            if len(response.text) == 0:
                raise Exception("Empty response")
            try:
                json_data = response.json()
            except Exception as e:
                _LOGGER.error(
                    'Failed to parse response: %s response: "%s"', e, response.text
                )
                return None
            if "XC_SUC" in json_data:
                return json_data["XC_SUC"]
            raise Exception(
                f'Failed to execute command: {response.status_code} "{response.text}"'
            )

    def get_all(self):
        return self.request_cmd({"XC_FNC": "GetAll"})

    def get_states(self):
        """Get the states of all devices connected to the system."""
        data = cast(
            SelveRawStates | None,
            self.request_cmd({"XC_FNC": "GetStates", "config": "1"}),
        )
        if data is None:
            return None
        states: SelveStates = {}
        for state in data:
            if state["type"] == "CM":
                try:
                    states[state["sid"]] = parseCommeoRawState(state)
                except Exception as e:
                    _LOGGER.error(
                        "Failed to parse Commeo state for sid %s: %s raw=%s",
                        state["sid"],
                        e,
                        state,
                    )
            elif state["type"] == "IV":
                states[state["sid"]] = state
            elif state["type"] == "SGROUP":
                state["name"] = (
                    base64.b64decode(state["name"])
                    .decode("utf-8", errors="replace")
                    .strip()
                )
                states[state["sid"]] = state
            elif state["type"] == "EVENT":
                pass
            else:
                _LOGGER.debug("Unknown state type: %s raw=%s", state["type"], state)
        return states

    def get_config(self, type: str, adr: str):
        """Get the configuration of a device by its RF address."""
        data = self.request_cmd({"XC_FNC": "GetConfig", "adr": adr, "type": type})
        if data:
            return data
        else:
            raise Exception(f"Failed to get config for address {adr}")

    def get_commeo_config(self, adr: str):
        """Get the configuration of a Commeo device by its RF address."""
        return self.get_config("CM", adr)

    def send_command(self, device_id: str, cmd: str, value: int | None = None):
        """id = device sid"""
        data: dict[str, Any] = {  # pyright: ignore[reportExplicitAny]
            "XC_FNC": "SendGenericCmd",
            "id": device_id,
            "data": {
                "cmd": cmd,
            },
        }
        if value is not None:
            data["data"]["value"] = value
        response = self.request("POST", "/cmd", data)
        return response


class UDPState(TypedDict):
    """Represents the UDP state update for a device."""

    state: dict[str, SelveRawCommeoDeviceState]
    ts: float


class DataStoreDict(TypedDict):
    """TypedDict for data stored in hass.data."""

    api: SeleveHomeServer
    server_info: ServerInfo
    devices: SelveStates | None
    udp_last: dict[str, UDPState]
    coordinator: DataUpdateCoordinator[SelveStates]
    udp_task: Task[None] | None
