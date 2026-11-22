"""Data update coordinator for CampChef integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CampChefAPI,
    CampChefAuthError,
    CampChefConnectionError,
    CampChefWebSocket,
    merge_grill_data,
)
from .const import DOMAIN, UPDATE_INTERVAL
from .const import MAX_SMOKE_LEVEL, MAX_TEMP_F, MIN_SMOKE_LEVEL, MIN_TEMP_F

_LOGGER = logging.getLogger(__name__)


class CampChefDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching CampChef data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: CampChefAPI,
        grill_id: str,
        grill_name: str,
    ) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.grill_id = grill_id
        self.grill_name = grill_name
        self.websocket: Optional[CampChefWebSocket] = None
        self._ws_data: Dict[str, Any] = {}
        self._probe_labels: Dict[int, str] = {}
        self._current_session_id: Optional[str] = None
        self._session_update_skip = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{grill_name}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API endpoint."""
        try:
            grill_data = await self.api.get_grill(self.grill_id)
            grill_data = await self._hydrate_session_data(grill_data)

            ws_probe_data = self._consume_ws_probe_data()
            if ws_probe_data:
                grill_data = merge_grill_data(grill_data, {"probes": ws_probe_data})

            ws_data = {
                key: value
                for key, value in self._ws_data.items()
                if key != "probes"
            }
            if ws_data:
                grill_data = merge_grill_data(grill_data, ws_data)

            grill_data = self._normalize_probe_snapshot(grill_data)

            # Start WebSocket connection if not already connected
            if not self.websocket or not self.websocket.running:
                await self._setup_websocket()

            return grill_data

        except CampChefAuthError as err:
            _LOGGER.warning("Authentication error for grill %s: %s", self.grill_id, err)
            # Find the config entry for this coordinator and trigger reauth
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.data.get("user_id"):  # This is our entry
                    entry.async_start_reauth(self.hass)
                    break
            raise UpdateFailed(f"Authentication failed: {err}") from err

        except CampChefConnectionError as err:
            _LOGGER.error("Connection error for grill %s: %s", self.grill_id, err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _hydrate_session_data(self, grill_data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge active cook-session updates into grill data."""
        current_session = grill_data.get("currentSession") or {}
        session_id = current_session.get("_id")
        if not session_id:
            self._current_session_id = None
            self._session_update_skip = 0
            return grill_data

        if session_id != self._current_session_id:
            self._current_session_id = session_id
            self._session_update_skip = 0

        page_size = 250
        skip = self._session_update_skip
        all_updates: list[Dict[str, Any]] = []

        while True:
            updates = await self.api.get_cook_session_updates(
                session_id,
                skip=skip,
                limit=page_size,
            )
            if not updates:
                break

            all_updates.extend(updates)
            skip += len(updates)

            if len(updates) < page_size:
                break

        if not all_updates:
            return self._normalize_probe_snapshot(grill_data)

        self._session_update_skip = skip
        session_payload = self._build_session_update_payload(all_updates)
        return self._normalize_probe_snapshot(merge_grill_data(grill_data, session_payload))

    def _build_session_update_payload(self, updates: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert cook-session updates to the legacy grill entity model."""
        payload: Dict[str, Any] = {}
        probe_map: Dict[int, Dict[str, Any]] = {}

        for update in updates:
            chamber_temp = update.get("chamber")
            if chamber_temp is not None:
                payload.setdefault("chamber", {})["temperature"] = chamber_temp

            chamber_goal = update.get("chamberGoal")

            mode = update.get("mode")
            if isinstance(mode, dict):
                payload["mode"] = {
                    "type": mode.get("mode") or mode.get("type"),
                    "temperature": mode.get("temperature", mode.get("goal")),
                    "smokeLevel": mode.get("smokeLevel"),
                    "fanLevel": mode.get("fanLevel"),
                }

            probe_index = update.get("probeNumber")
            if probe_index is None:
                continue

            probe = dict(probe_map.get(probe_index) or {"index": probe_index})

            probe_temp = update.get("probeTemp")
            if probe_temp is not None:
                if probe_temp > 0:
                    probe["temperature"] = probe_temp
                    probe.pop("_disconnected", None)
                else:
                    probe.pop("temperature", None)
                    probe["_disconnected"] = True
                    probe["_clear_target"] = True
            else:
                probe.pop("temperature", None)

            probe_goal = update.get("probeGoal")
            if probe_goal is not None:
                if probe_goal > 0:
                    probe["target"] = probe_goal
                    probe.pop("_clear_target", None)
                else:
                    probe.pop("target", None)
                    probe["_clear_target"] = True

            probe_name = update.get("probeName")
            if probe_name:
                probe["label"] = probe_name

            probe_map[probe_index] = probe

        if probe_map:
            payload["probes"] = [probe_map[index] for index in sorted(probe_map)]

        return payload

    def _consume_ws_probe_data(self) -> list[Dict[str, Any]]:
        """Consume probe WebSocket updates so stale values don't survive a poll."""
        probe_updates = self._ws_data.get("probes")
        if not probe_updates:
            return []

        self._ws_data["probes"] = []
        return [dict(probe) for probe in probe_updates if isinstance(probe, dict)]

    def _normalize_probe_snapshot(self, grill_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build a fresh probe snapshot for all four probe slots."""
        normalized = dict(grill_data)
        probe_map: Dict[int, Dict[str, Any]] = {}

        for probe in normalized.get("probes", []) or []:
            if not isinstance(probe, dict):
                continue
            probe_index = probe.get("index")
            if probe_index is None or not 0 <= probe_index < 4:
                continue
            probe_map[probe_index] = dict(probe)

        probes: list[Dict[str, Any]] = []
        for probe_index in range(4):
            current = dict(probe_map.get(probe_index) or {"index": probe_index})
            label = current.get("label") or self._probe_labels.get(probe_index)
            if label:
                self._probe_labels[probe_index] = label

            disconnected = bool(current.get("_disconnected"))
            temperature = current.get("temperature")
            has_valid_temperature = isinstance(temperature, (int, float)) and temperature > 0
            target = current.get("target")
            has_valid_target = isinstance(target, (int, float)) and target > 0
            clear_target = bool(current.get("_clear_target")) or disconnected or not has_valid_temperature

            probe_data: Dict[str, Any] = {"index": probe_index}
            if label:
                probe_data["label"] = label
            if has_valid_temperature and not disconnected:
                probe_data["temperature"] = temperature
            if has_valid_target and not clear_target:
                probe_data["target"] = target

            probes.append(probe_data)

        normalized["probes"] = probes
        return normalized

    async def _setup_websocket(self) -> None:
        """Set up WebSocket connection for real-time updates."""
        if not self.api.token:
            _LOGGER.warning("No token available for WebSocket connection")
            return

        try:
            self.websocket = CampChefWebSocket(
                self.grill_id,
                self.api.token,
                self._websocket_callback
            )
            await self.websocket.connect()
            _LOGGER.info(
                "WebSocket connected for grill %s (%s)",
                self.grill_name,
                self.grill_id
            )
        except Exception as err:
            _LOGGER.warning(
                "Failed to connect WebSocket for grill %s: %s. Continuing with REST API only.",
                self.grill_id,
                err
            )
            # Don't raise exception - continue working with REST API only
            self.websocket = None

    async def _websocket_callback(self, data: Dict[str, Any]) -> None:
        """Handle WebSocket messages."""
        _LOGGER.debug("WebSocket data received for grill %s: %s", self.grill_id, data)

        if not isinstance(data, dict):
            return

        # Parse WebSocket characteristic data
        characteristic = data.get("characteristic", {})
        char_name = characteristic.get("name")
        char_data = data.get("data")

        if not char_name or char_data is None:
            return

        # Map characteristic names to grill data structure
        if char_name == "Chamber":
            if "chamber" not in self._ws_data:
                self._ws_data["chamber"] = {}
            self._ws_data["chamber"]["temperature"] = char_data

        elif char_name == "Mode":
            normalized_mode = self._normalize_ws_mode(char_data)
            if normalized_mode is not None:
                self._ws_data["mode"] = normalized_mode

        elif char_name == "PelletLevel":
            self._ws_data["pelletLevel"] = char_data

        elif char_name.startswith("Probe"):
            # Extract probe index from name like "Probe0", "Probe1", etc.
            try:
                probe_index = int(char_name.replace("Probe", ""))
                if "probes" not in self._ws_data:
                    self._ws_data["probes"] = []

                # Ensure probes list is long enough
                while len(self._ws_data["probes"]) <= probe_index:
                    self._ws_data["probes"].append({"index": len(self._ws_data["probes"])})

                probe_data = self._ws_data["probes"][probe_index]
                if isinstance(char_data, (int, float)) and char_data > 0:
                    probe_data["temperature"] = char_data
                    probe_data.pop("_disconnected", None)
                else:
                    probe_data.pop("temperature", None)
                    probe_data["_disconnected"] = True
                    probe_data["_clear_target"] = True
            except (ValueError, IndexError):
                _LOGGER.warning("Invalid probe characteristic: %s", char_name)

        elif char_name == "WifiConnectionStatus":
            self._ws_data["wifiConnectionStatus"] = char_data

        elif char_name == "LastFault":
            self._ws_data["lastFault"] = char_data

        elif char_name == "WifiRssi":
            self._ws_data["wifiRssi"] = char_data

        # Trigger coordinator update to notify entities
        updated_data = merge_grill_data(self.data or {}, self._ws_data)
        self.async_set_updated_data(self._normalize_probe_snapshot(updated_data))

    def _normalize_ws_mode(self, mode_data: Any) -> Optional[Dict[str, Any]]:
        """Validate websocket mode payloads before merging them into entity state."""
        if not isinstance(mode_data, dict):
            return None

        mode_type = mode_data.get("type")
        if mode_type in {"STANDBY", "IDLE"}:
            return {"type": mode_type}

        if mode_type == "FAN":
            fan_level = mode_data.get("fanLevel")
            if isinstance(fan_level, int):
                return {"type": mode_type, "fanLevel": fan_level}
            return None

        if mode_type != "RUN":
            return None

        temperature = mode_data.get("temperature")
        smoke_level = mode_data.get("smokeLevel")
        if not isinstance(temperature, int) or not MIN_TEMP_F <= temperature <= MAX_TEMP_F:
            _LOGGER.debug("Ignoring websocket mode with implausible temperature: %s", mode_data)
            return None
        if not isinstance(smoke_level, int) or not MIN_SMOKE_LEVEL <= smoke_level <= MAX_SMOKE_LEVEL:
            _LOGGER.debug("Ignoring websocket mode with implausible smoke level: %s", mode_data)
            return None

        return {
            "type": mode_type,
            "temperature": temperature,
            "smokeLevel": smoke_level,
        }

    async def async_set_grill_mode(
        self,
        mode_type: str,
        temperature: Optional[int] = None,
        smoke_level: Optional[int] = None,
        fan_level: Optional[int] = None,
    ) -> None:
        """Set grill mode."""
        try:
            mode_payload: Dict[str, Any] = {"type": mode_type}
            if mode_type == "RUN" and temperature is not None:
                mode_payload["temperature"] = temperature
                if smoke_level is not None:
                    mode_payload["smokeLevel"] = smoke_level
            elif mode_type == "FAN" and fan_level is not None:
                mode_payload["fanLevel"] = fan_level

            if self.websocket and self.websocket.running:
                await self.websocket.send_command(
                    {"wsId": 1, "name": "Mode"},
                    mode_payload,
                )
                self._ws_data["mode"] = mode_payload
            else:
                await self.api.set_grill_mode(
                    self.grill_id, mode_type, temperature, smoke_level, fan_level
                )
            # Trigger immediate data refresh
            await self.async_request_refresh()
        except (CampChefAuthError, CampChefConnectionError) as err:
            _LOGGER.error("Failed to set grill mode: %s", err)
            raise

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and close connections."""
        if self.websocket:
            await self.websocket.disconnect()
            self.websocket = None
