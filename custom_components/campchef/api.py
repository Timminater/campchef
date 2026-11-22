"""CampChef API client."""
import asyncio
import inspect
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp
import websockets

from .const import API_BASE_URL, WS_BASE_URL

_LOGGER = logging.getLogger(__name__)


class CampChefAuthError(Exception):
    """Authentication error."""


class CampChefConnectionError(Exception):
    """Connection error."""


def normalize_grill_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize CampChef API payloads to the structure used by entities."""
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    current_session = normalized.get("currentSession") or {}
    session_mode = current_session.get("mode") or {}

    existing_mode = normalized.get("mode") if isinstance(normalized.get("mode"), dict) else {}
    legacy_mode = dict(existing_mode)
    session_mode_type = session_mode.get("mode") or session_mode.get("type")
    if session_mode_type and not legacy_mode.get("type"):
        legacy_mode["type"] = session_mode_type
    if legacy_mode.get("temperature") is None:
        legacy_mode["temperature"] = session_mode.get("temperature", session_mode.get("goal"))
    if legacy_mode.get("smokeLevel") is None:
        legacy_mode["smokeLevel"] = session_mode.get("smokeLevel")
    if legacy_mode.get("fanLevel") is None:
        legacy_mode["fanLevel"] = session_mode.get("fanLevel")
    if legacy_mode:
        normalized["mode"] = legacy_mode

    existing_chamber = normalized.get("chamber") if isinstance(normalized.get("chamber"), dict) else {}
    chamber = dict(existing_chamber)
    if chamber.get("target") is None:
        session_target = session_mode.get("temperature", session_mode.get("goal"))
        if session_target is not None:
            chamber["target"] = session_target
    if chamber:
        normalized["chamber"] = chamber

    probe_map: Dict[int, Dict[str, Any]] = {}
    for index, probe in enumerate(normalized.get("probes") or []):
        if not isinstance(probe, dict):
            continue
        probe_index = probe.get("index", index)
        probe_map[probe_index] = dict(probe)

    probe_goals = current_session.get("probeGoals") or []
    probe_names = current_session.get("probeNames") or []
    for probe_index in range(max(len(probe_goals), len(probe_names))):
        probe = dict(probe_map.get(probe_index) or {"index": probe_index})
        if probe.get("target") is None and probe_index < len(probe_goals):
            probe_goal = probe_goals[probe_index]
            if isinstance(probe_goal, (int, float)) and probe_goal > 0:
                probe["target"] = probe_goal
        if not probe.get("label") and probe_index < len(probe_names) and probe_names[probe_index]:
            probe["label"] = probe_names[probe_index]
        probe_map[probe_index] = probe

    if probe_map:
        normalized["probes"] = [probe_map[index] for index in sorted(probe_map)]

    if normalized.get("lastFault") is None and current_session.get("fault") is not None:
        normalized["lastFault"] = current_session.get("fault")

    if normalized.get("wifiConnectionStatus") is None and normalized.get("isOnline") is not None:
        normalized["wifiConnectionStatus"] = "WEBSOCKET" if normalized["isOnline"] else "OFFLINE"

    if normalized.get("wifiRssi") is None and normalized.get("lastWifiRssi") is not None:
        normalized["wifiRssi"] = normalized.get("lastWifiRssi")

    if normalized.get("firmwareVersion") is None:
        versions = [value for value in (normalized.get("mcuVersion"), normalized.get("wifiVersion")) if value]
        if versions:
            normalized["firmwareVersion"] = " / ".join(versions)

    return normalized


def merge_grill_data(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial grill updates into the full grill payload."""
    merged = dict(base or {})

    for key, value in (updates or {}).items():
        if key in {"mode", "chamber"} and isinstance(value, dict):
            existing = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**existing, **value}
            continue

        if key == "probes" and isinstance(value, list):
            probe_map: Dict[int, Dict[str, Any]] = {}
            for index, probe in enumerate(merged.get("probes") or []):
                if isinstance(probe, dict):
                    probe_index = probe.get("index", index)
                    probe_map[probe_index] = dict(probe)
            for index, probe in enumerate(value):
                if not isinstance(probe, dict):
                    continue
                probe_index = probe.get("index", index)
                existing_probe = dict(probe_map.get(probe_index) or {"index": probe_index})
                existing_probe.update(probe)
                probe_map[probe_index] = existing_probe
            merged["probes"] = [probe_map[index] for index in sorted(probe_map)]
            continue

        merged[key] = value

    return normalize_grill_data(merged)


class CampChefAPI:
    """CampChef API client."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self.session = session
        self.token: Optional[str] = None
        self.base_url = API_BASE_URL
        self.ws_url = WS_BASE_URL

    async def send_email_login(self, email: str) -> bool:
        """Send email login request to trigger PIN email."""
        _LOGGER.debug("Sending email login request for: %s", email)
        try:
            async with self.session.post(
                f"{self.base_url}/auth/login-email",
                json=email,
                headers={"Content-Type": "application/json"}
            ) as response:
                response_text = await response.text()
                _LOGGER.debug("Email login response status: %s", response.status)
                _LOGGER.debug("Email login response: %s", response_text)

                if response.status in (200, 204):
                    _LOGGER.info("Email login successful for: %s (status: %s)", email, response.status)
                    return True
                else:
                    _LOGGER.error("Email login failed with status: %s", response.status)
                    _LOGGER.error("Response: %s", response_text)
                    raise CampChefAuthError(f"Email login failed: {response.status}")

        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error during email login: %s", err)
            raise CampChefConnectionError(f"Connection error: {err}") from err

    async def verify_pin(self, email: str, pin: str) -> bool:
        """Verify PIN and get authentication token."""
        try:
            async with self.session.post(
                f"{self.base_url}/auth/login-email-pin",
                json={"email": email, "pin": pin},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    token_text = await response.text()
                    self.token = token_text.strip('"')
                    return True
                elif response.status == 401:
                    raise CampChefAuthError("Invalid PIN")
                else:
                    response_text = await response.text()
                    _LOGGER.error("PIN verification failed: %s - %s", response.status, response_text)
                    raise CampChefAuthError(f"PIN verification failed: {response.status}")

        except aiohttp.ClientError as err:
            raise CampChefConnectionError(f"Connection error: {err}") from err

    async def authenticate(self, email: str, pin: str) -> bool:
        """Full authentication with email and PIN."""
        await self.send_email_login(email)
        return await self.verify_pin(email, pin)

    async def refresh_token(self) -> bool:
        """Refresh the authentication token."""
        if not self.token:
            return False

        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            async with self.session.get(
                f"{self.base_url}/auth/refresh-token",
                headers=headers
            ) as response:
                if response.status == 200:
                    token_text = await response.text()
                    self.token = token_text.strip('"')
                    return True
                return False
        except aiohttp.ClientError:
            return False

    async def get_user_profile(self) -> Dict[str, Any]:
        """Get user profile information."""
        if not self.token:
            raise CampChefAuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with self.session.get(
            f"{self.base_url}/auth/self",
            headers=headers
        ) as response:
            if response.status == 401:
                raise CampChefAuthError("Token expired")
            response.raise_for_status()
            return await response.json()

    async def get_grills(self) -> List[Dict[str, Any]]:
        """Get all grills for the authenticated user."""
        if not self.token:
            raise CampChefAuthError("Not authenticated")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        query_data = {
            "filter": {},
            "sort": {},
            "limit": 100,
            "skip": 0
        }

        async with self.session.post(
            f"{self.base_url}/grill/rest/query",
            json=query_data,
            headers=headers
        ) as response:
            if response.status == 401:
                raise CampChefAuthError("Token expired")
            response.raise_for_status()
            data = await response.json()
            _LOGGER.debug("Grills API response: %s", data)
            _LOGGER.debug("Response type: %s", type(data))

            # Handle different response formats
            if isinstance(data, list):
                return [normalize_grill_data(grill) for grill in data]
            elif isinstance(data, dict):
                return [normalize_grill_data(grill) for grill in data.get("data", [])]
            else:
                _LOGGER.warning("Unexpected grills response format: %s", data)
                return []

    async def get_grill(self, grill_id: str) -> Dict[str, Any]:
        """Get specific grill details."""
        if not self.token:
            raise CampChefAuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with self.session.get(
            f"{self.base_url}/grill/rest/{grill_id}",
            headers=headers
        ) as response:
            if response.status == 401:
                raise CampChefAuthError("Token expired")
            response.raise_for_status()
            return normalize_grill_data(await response.json())

    async def get_cook_session_updates(
        self,
        session_id: str,
        skip: int = 0,
        limit: int = 250,
    ) -> List[Dict[str, Any]]:
        """Get cook session updates for a running session."""
        if not self.token:
            raise CampChefAuthError("Not authenticated")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "filter": {"session": session_id},
            "limit": limit,
            "skip": skip,
        }

        async with self.session.post(
            f"{self.base_url}/cook-session-update/rest/query",
            json=payload,
            headers=headers,
        ) as response:
            if response.status == 401:
                raise CampChefAuthError("Token expired")
            response.raise_for_status()
            data = await response.json()
            return data if isinstance(data, list) else []

    async def update_grill(self, grill_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update grill settings."""
        if not self.token:
            raise CampChefAuthError("Not authenticated")

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        async with self.session.patch(
            f"{self.base_url}/grill/rest/{grill_id}",
            json=data,
            headers=headers
        ) as response:
            if response.status == 401:
                raise CampChefAuthError("Token expired")
            response.raise_for_status()
            return await response.json()

    async def set_grill_mode(
        self,
        grill_id: str,
        mode_type: str,
        temperature: Optional[int] = None,
        smoke_level: Optional[int] = None,
        fan_level: Optional[int] = None
    ) -> Dict[str, Any]:
        """Set grill operating mode."""
        mode_data = {"type": mode_type}

        if mode_type == "RUN" and temperature is not None:
            mode_data["temperature"] = temperature
            if smoke_level is not None:
                mode_data["smokeLevel"] = smoke_level
        elif mode_type == "FAN" and fan_level is not None:
            mode_data["fanLevel"] = fan_level

        return await self.update_grill(grill_id, {"mode": mode_data})


class CampChefWebSocket:
    """CampChef WebSocket client for real-time updates."""

    def __init__(self, grill_id: str, token: str, callback = None) -> None:
        """Initialize WebSocket client."""
        self.grill_id = grill_id
        self.token = token
        self.callback = callback
        self.websocket = None
        self.running = False

    async def connect(self) -> None:
        """Connect to WebSocket."""
        # Try different WebSocket URL formats based on documentation
        ws_urls = [
            # Newer backend expects the path parameter style
            f"{WS_BASE_URL}?path=client-socket/{self.grill_id}&jwt={self.token}",
            f"{WS_BASE_URL}/client-socket/{self.grill_id}?jwt={self.token}",
            f"{WS_BASE_URL}?path=multiplex&jwt={self.token}",
            f"{WS_BASE_URL}/client-socket/{self.grill_id}",
        ]

        last_error = None
        for ws_url in ws_urls:
            try:
                _LOGGER.debug("Attempting WebSocket connection to: %s", ws_url)

                # Add headers for authentication if URL doesn't have jwt param
                headers = {}
                if "jwt=" not in ws_url:
                    headers["Authorization"] = f"Bearer {self.token}"

                connect_kwargs = {}
                if headers:
                    connect_signature = inspect.signature(websockets.connect)
                    header_argument = (
                        "additional_headers"
                        if "additional_headers" in connect_signature.parameters
                        else "extra_headers"
                    )
                    connect_kwargs[header_argument] = headers

                self.websocket = await websockets.connect(ws_url, **connect_kwargs)
                self.running = True
                _LOGGER.info("WebSocket connected for grill %s using URL: %s", self.grill_id, ws_url)

                # Start listening for messages
                asyncio.create_task(self._listen())
                return

            except Exception as err:
                last_error = err
                _LOGGER.debug("WebSocket connection failed for URL %s: %s", ws_url, err)
                continue

        # If all URLs failed
        _LOGGER.error("All WebSocket connection attempts failed. Last error: %s", last_error)
        raise CampChefConnectionError(f"WebSocket connection failed: {last_error}")

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            _LOGGER.debug("WebSocket disconnected for grill %s", self.grill_id)

    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        try:
            while self.running and self.websocket:
                message = await self.websocket.recv()
                if self.callback:
                    await self._handle_ws_message(message)

        except websockets.exceptions.ConnectionClosed:
            _LOGGER.debug("WebSocket connection closed for grill %s", self.grill_id)
        except Exception as err:
            _LOGGER.error("WebSocket error for grill %s: %s", self.grill_id, err)
        finally:
            self.running = False

    async def _handle_ws_message(self, message: Any) -> None:
        """Process a single WebSocket frame."""
        try:
            # JSON payloads
            if isinstance(message, str) and message.startswith("{"):
                data = json.loads(message)
                await self.callback(data)
                return

            # Handle multi-frame base64 strings separated by '|'
            if isinstance(message, str):
                parts = message.split("|")
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    decoded = self._decode_base64(part)
                    if decoded:
                        await self._maybe_callback(decoded)
                return

            # Handle raw binary or partially base64 payloads
            if isinstance(message, (bytes, bytearray)):
                await self._maybe_callback(bytes(message))
                return

        except Exception as err:
            _LOGGER.debug("Failed to handle WebSocket message %s: %s", message, err)

    async def _maybe_callback(self, raw: bytes) -> None:
        """Decode a raw binary frame and invoke callback if valid."""
        decoded_data = self._decode_binary_message(raw)
        if decoded_data:
            await self.callback(decoded_data)

    def _decode_base64(self, data: str) -> Optional[bytes]:
        """Decode a base64 string, return None on failure."""
        try:
            import base64
            return base64.b64decode(data, validate=False)
        except Exception:
            return None

    def _decode_binary_message(self, data: bytes) -> Dict[str, Any]:
        """Decode binary WebSocket message according to CampChef protocol."""
        if len(data) < 3:
            return None

        try:
            ws_id = data[0]
            message_type = data[1]
            payload = data[2:]

            # Map WS IDs to characteristic names based on documentation
            characteristic_map = {
                1: "Mode",
                2: "GrillModel",
                3: "Transitioning",
                4: "PelletLevel",
                5: "LastFault",
                6: "Chamber",
                8: "ClientSecret",
                9: "WebId",
                32: "WifiSsid",
                33: "WifiPassword",
                34: "WifiConnectionStatus",
                35: "WifiIsScanning",
                36: "WifiScanResult",
                37: "WifiRssi",
                38: "HttpUrl",
                39: "WsUrl",
                48: "StmFirmware",
                49: "EspFirmware",
                50: "UpdateEsp",
                51: "UpdateStm",
                52: "OtaProgress",
            }

            # Add probe characteristics (16-31)
            for i in range(16):
                characteristic_map[16 + i] = f"Probe{i}"

            characteristic_name = characteristic_map.get(ws_id, f"Unknown_{ws_id}")

            # Decode payload based on characteristic type
            decoded_value = self._decode_payload(characteristic_name, payload)

            return {
                "characteristic": {
                    "wsId": ws_id,
                    "name": characteristic_name
                },
                "data": decoded_value,
                "isNotify": message_type == 4,  # Type 4 = Notify/continuous data
                "status": None
            }

        except Exception as err:
            _LOGGER.debug("Failed to decode binary message: %s", err)
            return None

    def _decode_payload(self, characteristic_name: str, payload: bytes) -> Any:
        """Decode payload based on characteristic type."""
        try:
            if characteristic_name in ["Chamber"] or characteristic_name.startswith("Probe"):
                # Temperature values - little-endian 16-bit integers
                if len(payload) >= 2:
                    # Try both byte orders; pick the most reasonable Fahrenheit value
                    val_le = int.from_bytes(payload[:2], byteorder='little', signed=True)
                    val_be = int.from_bytes(payload[:2], byteorder='big', signed=True)
                    # Prefer a plausible Fahrenheit temp (0-1000F)
                    for candidate in (val_be, val_le):
                        if 0 <= candidate <= 1000:
                            return candidate
                    return val_le

            elif characteristic_name == "PelletLevel":
                # Byte value (0-100)
                return payload[0] if len(payload) > 0 else None

            elif characteristic_name in ["Transitioning", "WifiIsScanning"]:
                # Boolean values
                return payload[0] == 1 if len(payload) > 0 else None

            elif characteristic_name in ["WifiSsid", "WifiPassword", "WebId", "HttpUrl", "WsUrl", "StmFirmware", "EspFirmware"]:
                # String values - UTF-8 encoded
                return payload.decode('utf-8').rstrip('\x00')

            elif characteristic_name == "WifiConnectionStatus":
                # Enum values
                status_map = {0: "OFFLINE", 1: "ACCESS_POINT", 2: "WEBSOCKET", 3: "CREDENTIAL_FAIL"}
                return status_map.get(payload[0], "UNKNOWN") if len(payload) > 0 else None

            elif characteristic_name == "WifiRssi":
                if len(payload) > 0:
                    return int.from_bytes(payload[:1], byteorder='big', signed=True)
                return None

            elif characteristic_name == "LastFault":
                if len(payload) >= 2:
                    fault_id = payload[0]
                    fault_code = payload[1]
                    fault_map = {
                        0: "NONE",
                        1: "SENSOR",
                        2: "FLAME_OUT",
                        3: "OVER_TEMP",
                    }
                    return {
                        "id": fault_id,
                        "code": fault_map.get(fault_code, f"UNKNOWN_{fault_code}"),
                    }
                return None

            elif characteristic_name == "Mode":
                # Complex mode structure
                if len(payload) >= 1:
                    mode_type = payload[0]
                    if mode_type == 0x00:
                        return {"type": "STANDBY"}
                    elif mode_type == 0x11 and len(payload) >= 4:
                        temp_le = int.from_bytes(payload[1:3], byteorder='little')
                        temp_be = int.from_bytes(payload[1:3], byteorder='big')
                        temp = next(
                            (
                                candidate
                                for candidate in (temp_be, temp_le)
                                if 0 <= candidate <= 1000
                            ),
                            temp_le,
                        )
                        smoke = payload[3]
                        return {"type": "RUN", "temperature": temp, "smokeLevel": smoke}
                    elif mode_type == 0x01 and len(payload) >= 2:
                        fan_level = payload[1]
                        return {"type": "FAN", "fanLevel": fan_level}

            else:
                # Default: return raw bytes as hex
                return payload.hex()

        except Exception:
            # Fallback: return raw bytes as hex
            return payload.hex()

        return None

    async def send_command(self, characteristic: Dict[str, Any], data: Any) -> None:
        """Send command through WebSocket."""
        if not self.websocket or not self.running:
            raise CampChefConnectionError("WebSocket not connected")

        try:
            # CampChef expects the binary protocol as a base64-encoded text frame.
            ws_id = characteristic.get("wsId", 1)
            binary_message = self._encode_binary_command(ws_id, data)
            if not binary_message:
                raise CampChefConnectionError("Failed to encode binary command")

            import base64

            encoded_message = base64.b64encode(binary_message).decode("ascii")
            await self.websocket.send(encoded_message)
            _LOGGER.debug("Sent base64 WebSocket command: wsId=%s, data=%s", ws_id, data)

        except Exception as err:
            _LOGGER.error("Failed to send WebSocket command: %s", err)
            raise CampChefConnectionError(f"Failed to send command: {err}")

    def _encode_binary_command(self, ws_id: int, data: Any) -> bytes:
        """Encode command data to binary format according to CampChef protocol."""
        try:
            message_type = 2  # Type 2 = Write command
            payload = b''

            if ws_id == 1:  # Mode characteristic
                if isinstance(data, dict):
                    mode_type = data.get("type", "STANDBY")
                    if mode_type == "STANDBY":
                        payload = bytes([0x00])
                    elif mode_type == "RUN":
                        temp = data.get("temperature", 225)
                        smoke = data.get("smokeLevel", 5)
                        payload = (
                            bytes([0x11])
                            + temp.to_bytes(2, "big")
                            + bytes([smoke, 0])
                        )
                    elif mode_type == "FAN":
                        fan_level = data.get("fanLevel", 3)
                        payload = bytes([0x01, fan_level])
                    elif mode_type == "IDLE":
                        payload = bytes([0x02])  # Assuming IDLE is 0x02

            elif ws_id in range(16, 32):  # Probe targets
                if isinstance(data, (int, float)):
                    payload = int(data).to_bytes(2, 'little')

            else:
                # For other characteristics, convert data to string and encode
                if isinstance(data, str):
                    payload = data.encode('utf-8')
                elif isinstance(data, (int, float)):
                    payload = int(data).to_bytes(2, 'little')
                elif isinstance(data, bool):
                    payload = bytes([1 if data else 0])

            # Construct final message: [WS_ID][MESSAGE_TYPE][PAYLOAD]
            return bytes([ws_id, message_type]) + payload

        except Exception as err:
            _LOGGER.debug("Failed to encode binary command: %s", err)
            return None
