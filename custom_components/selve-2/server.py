import requests
from dataclasses import dataclass
from typing import Dict, Any, Optional
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


ETYPE_LABELS: Dict[int, str] = {
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


def label_for_e_type(code: Optional[int]) -> Optional[str]:
    if code is None:
        return None
    if code in ETYPE_LABELS:
        return ETYPE_LABELS[code]
    if 8 <= code <= 15:
        return "Unknown Motor Type"
    if 22 <= code <= 31:
        return "Unknown Switch Type"
    return "Unknown"


def parse_e_type(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = data.get("eType")
    if raw is None:
        return data
    try:
        code = int(raw)
    except (ValueError, TypeError):
        code = None
    data["eType_code"] = code
    data["eType"] = label_for_e_type(code)
    return data


@dataclass
class ServerInfo:
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

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "ServerInfo":
        """Create a ServerInfo instance from the API response."""
        data["name"] = fix_mojibake(data.get("name", ""))
        return cls(**data)


class CommeoDeviceType(IntEnum):
    """Enum representing the types of Commeo devices."""
    RECEIVER = 0
    SENSOR = 1

    @classmethod
    def parse_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if "deviceType" in data and data["deviceType"] is not None:
            try:
                data["deviceType"] = CommeoDeviceType(int(data["deviceType"]))
            except (ValueError, TypeError):
                # Handle unknown deviceType values
                data["deviceType"] = None
        return data


@dataclass
class CommeoReceiverState:
    sid: str  # HEX
    """The device id inside the gateway"""
    adr: str  # HEX
    """The RF address of the device"""
    deviceType: CommeoDeviceType
    """Device is either receiver or sensor. 00: Receiver or actuator and 01 for sensors"""
    eType: str
    """Human readable receiver type label (e.g. 'Inside Blind')."""
    eType_code: int
    """Original numeric eType code."""
    cid: str  # HEX
    """Commeo ID : The id of the device in CC. (not relevent for operations)"""
    state: Dict[str, Any]
    """"""
    group: int  # HEX
    """Group Sid. Only set if device is part of a group"""
    name: str = ""
    """Name of the device"""
    type: str = "CM"

    @classmethod
    def _parse_flags(self, flags: int) -> Dict[str, Any]:
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
        # Reverse the 16-bit bit order
        flags16 = flags & 0xFFFF
        rev_bits = int(f"{flags16:016b}"[::-1], 2)

        parsed_flags = {
            "timeout": bool(rev_bits & (1 << 0)),
            "overload": bool(rev_bits & (1 << 1)),
            "obstacle": bool(rev_bits & (1 << 2)),
            "emergency_alarm": bool(rev_bits & (1 << 3)),
            "sensor_learned": bool(rev_bits & (1 << 4)),
            "sensor_connected": not bool(rev_bits & (1 << 5)),
            "automatic_mode": bool(rev_bits & (1 << 6)),
            "cc_timeout": bool(rev_bits & (1 << 7)),
            "wind_alarm": bool(rev_bits & (1 << 8)),
            "rain_alarm": bool(rev_bits & (1 << 9)),
            "frost_alarm": bool(rev_bits & (1 << 10)),
        }
        return parsed_flags

    @classmethod
    def _parse_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the state data from the API response."""
        flags = state["flags"]
        if (flags != "-"):
            parsed_flags = self._parse_flags(int(flags, 10))
            state["attributes"] = parsed_flags
        # del state["flags"]
        return state

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "CommeoReceiverState":
        """Create a CommeoReceiverState instance from the API response."""
        # Convert eType from str/int to our enum
        if (data["type"] != "CM"):
            raise ValueError("Invalid type")
        del data["type"]
        data = parse_e_type(data)
        data = CommeoDeviceType.parse_data(data)
        if "group" in data and data["group"] is not None:
            data["group"] = int(data["group"])
        state = cls._parse_state(data["state"])
        del data["state"]
        return cls(state=state, **data)


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

IveoSwitchCommands = [
    "on",
    "off"
]

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


@dataclass
class CommeoSensorState:
    sid: str  # HEX
    adr: str  # HEX
    deviceType: CommeoDeviceType
    # eType: str ?
    cid: str  # HEX
    state: Dict[str, Any]
    name: str = ""
    """Name of the device"""
    type: str = "CM"

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "CommeoSensorState":
        """Create a CommeoSensorState instance from the API response."""
        if (data["type"] != "CM"):
            raise ValueError("Invalid type")
        del data["type"]
        data = CommeoDeviceType.parse_data(data)
        if "group" in data and data["group"] is not None:
            data["group"] = int(data["group"])
        return cls(**data)


class DeviceGroupdDeviceType(IntEnum):
    """Enum representing the types of Commeo devices."""
    SWITCH = 1
    MOTOR = 2

    @classmethod
    def parse_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if "deviceType" in data and data["deviceType"] is not None:
            try:
                data["deviceType"] = DeviceGroupdDeviceType(
                    int(data["deviceType"]))
            except (ValueError, TypeError):
                # Handle unknown deviceType values
                data["deviceType"] = None
        return data


@dataclass
class DeviceGroupState:
    """Represents the groups state."""
    name: str
    """Name of the group"""
    sid: str  # HEX
    """Group System ID"""
    adr: str  # HEX
    """Group Address"""
    deviceType: DeviceGroupdDeviceType
    sys: str
    """CM: Commeo Group IV: Iveo Group"""
    type: str = "SGROUP"

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "DeviceGroupState":
        """Create a DeviceGroupState instance from the API response.

        Decodes base64 encoded group names.
        """
        if data.get("type") != "SGROUP":
            raise ValueError("Invalid type")
        data = dict(data)  # shallow copy
        del data["type"]
        data = DeviceGroupdDeviceType.parse_data(data)
        # Decode base64 name if present
        name_val = data.get("name")
        if name_val:
            try:
                decoded = base64.b64decode(name_val).decode(
                    "utf-8", errors="replace")
                data["name"] = decoded
            except Exception as err:
                _LOGGER.debug(
                    "Failed to decode group name '%s': %s", name_val, err)
        return cls(**data)


@dataclass
class IveoReceiverState:
    """Represents an Iveo receiver device.

    The API returns for Iveo receivers:
    {"type":"IV", "sid":"04", "adr":"01", "config":"103,202", "state":"?"}
    """
    sid: str
    adr: str
    config: str
    state: str
    type: str = "IV"

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "IveoReceiverState":
        if data.get("type") != "IV":
            raise ValueError("Invalid type")
        data = dict(data)
        del data["type"]
        return cls(**data)


class SeleveHomeServer:
    def __init__(self, host, password):
        if not (host.startswith("https://") or host.startswith("http://")):
            host = f"http://{host}"
        self.host = host
        self.password = password

    def request(self, method, path, data=None):
        url = f"{self.host}{path}"
        url += f"?auth={self.password}"
        response = requests.request(method, url, json=data)
        return response

    def get_server_info(self) -> ServerInfo:
        response = self.request("GET", "/info")
        if response.status_code == 200 and "XC_SUC" in response.json():
            return ServerInfo.from_response(response.json()["XC_SUC"])
        else:
            raise Exception(
                f"Failed to get server info: {response.status_code} " f"{response.text}"
            )

    def request_cmd(self, params: Dict[str, Any]):
        params["auth"] = self.password
        url = "/cmd?auth=" + self.password
        url += "&" + "&".join(f"{k}={v}" for k, v in params.items())
        response = self.request("GET", url)
        if response.status_code == 200 and "XC_SUC" in response.json():
            return response.json()["XC_SUC"]
        else:
            raise Exception(
                f"Failed to execute command: {response.status_code} " f"{response.text}"
            )

    def get_all(self):
        return self.request_cmd({"XC_FNC": "GetAll"})

    def get_states(self):
        """Get the states of all devices connected to the system."""
        data = self.request_cmd({"XC_FNC": "GetStates", "config": "1"})
        states = {}
        for state in data:
            stateType = state["type"]
            deviceType = state.get("deviceType", "")
            if stateType == "CM":
                if deviceType == "00":  # Commeo Receivers
                    receiver = CommeoReceiverState.from_response(state)
                    receiver.name = fix_mojibake(receiver.name)
                    states[receiver.sid] = receiver
                elif deviceType == "01":  # Commeo Sensors
                    sensor = CommeoSensorState.from_response(state)
                    sensor.name = fix_mojibake(sensor.name)
                    states[sensor.sid] = sensor
                else:
                    _LOGGER.error(
                        "Unknown Commeo deviceType: %s raw=%s", deviceType, state)
            elif stateType == "IV":
                iveo = IveoReceiverState.from_response(state)
                states[iveo.sid] = iveo
            elif stateType == "SGROUP":
                group = DeviceGroupState.from_response(state)
                group.name = fix_mojibake(group.name)
                states[group.sid] = group
            elif stateType == "EVENT":
                pass
            else:
                _LOGGER.debug("Unknown state type: %s raw=%s",
                              stateType, state)
        return states

    def get_config(self, type: str, adr: str):
        """Get the configuration of a device by its RF address."""
        data = self.request_cmd(
            {"XC_FNC": "GetConfig", "adr": adr, "type": type})
        if data:
            return data
        else:
            raise Exception(f"Failed to get config for address {adr}")

    def get_commeo_config(self, adr: str):
        """Get the configuration of a Commeo device by its RF address."""
        return self.get_config("CM", adr)

    def send_command(self, device_id: str, cmd: str, value=None):
        """id = device sid"""
        data: dict = {
            "XC_FNC": "SendGenericCmd",
            "id": device_id,
            "data": {
                "cmd": cmd,
            }
        }
        if value is not None:
            data["data"]["value"] = value
        response = self.request("POST", "/cmd", data)
        return response
