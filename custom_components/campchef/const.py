"""Constants for the CampChef integration."""

DOMAIN = "campchef"

# API Configuration
API_BASE_URL = "https://api.campchef.app"
WS_BASE_URL = "wss://ws.api.campchef.app"

# Configuration Flow
CONF_EMAIL = "email"
CONF_PIN = "pin"

# Default values
DEFAULT_SCAN_INTERVAL = 30
UPDATE_INTERVAL = 30

# Grill modes
GRILL_MODE_STANDBY = "STANDBY"
GRILL_MODE_RUN = "RUN"
GRILL_MODE_FAN = "FAN"
GRILL_MODE_IDLE = "IDLE"

GRILL_MODES = [
    GRILL_MODE_STANDBY,
    GRILL_MODE_RUN,
    GRILL_MODE_FAN,
    GRILL_MODE_IDLE,
]

# WiFi connection statuses
WIFI_STATUS_OFFLINE = "OFFLINE"
WIFI_STATUS_ACCESS_POINT = "ACCESS_POINT"
WIFI_STATUS_WEBSOCKET = "WEBSOCKET"
WIFI_STATUS_CREDENTIAL_FAIL = "CREDENTIAL_FAIL"

WIFI_STATUSES = [
    WIFI_STATUS_OFFLINE,
    WIFI_STATUS_ACCESS_POINT,
    WIFI_STATUS_WEBSOCKET,
    WIFI_STATUS_CREDENTIAL_FAIL,
]

# Fault codes
FAULT_NONE = "NONE"
FAULT_SENSOR = "SENSOR"
FAULT_FLAME_OUT = "FLAME_OUT"
FAULT_OVER_TEMP = "OVER_TEMP"

FAULT_CODES = [
    FAULT_NONE,
    FAULT_SENSOR,
    FAULT_FLAME_OUT,
    FAULT_OVER_TEMP,
]

# Grill models
GRILL_MODELS = [
    "WOODWIND_PRO_24",
    "WOODWIND_PRO_36",
    "PURSUIT_20",
    "PURSUIT_24",
]

# Models without a pellet level sensor in the CampChef cloud payload
MODELS_WITHOUT_PELLET_SENSOR = {
    "CDLX",
}

# Temperature limits
MIN_TEMP_F = 160
MAX_TEMP_F = 500
DEFAULT_TEMP_F = 225

# Smoke levels
MIN_SMOKE_LEVEL = 1
MAX_SMOKE_LEVEL = 10
DEFAULT_SMOKE_LEVEL = 5

# Fan levels
MIN_FAN_LEVEL = 1
MAX_FAN_LEVEL = 5
DEFAULT_FAN_LEVEL = 3

# Entity unique ID prefixes
UNIQUE_ID_GRILL = "grill"
UNIQUE_ID_CHAMBER_TEMP = "chamber_temp"
UNIQUE_ID_PELLET_LEVEL = "pellet_level"
UNIQUE_ID_PROBE = "probe"
UNIQUE_ID_WIFI_SIGNAL = "wifi_signal"
UNIQUE_ID_CONNECTION_STATUS = "connection_status"
UNIQUE_ID_FAULT_STATUS = "fault_status"

# Platforms
PLATFORMS = ["sensor", "climate", "binary_sensor"]
