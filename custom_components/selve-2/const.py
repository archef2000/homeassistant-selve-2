DOMAIN = "selve"

CONF_HOST = "host"
CONF_PASSWORD = "password"
CONF_NAME = "server_name"

DATA_API = "api"
DATA_SERVER_INFO = "server_info"
DATA_DEVICES = "devices"

PLATFORMS: list[str] = ["cover", "binary_sensor"]

DEFAULT_UPDATE_INTERVAL = 30  # seconds

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT = 1901
