"""Climate platform for CampChef integration."""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DEFAULT_SMOKE_LEVEL,
    DEFAULT_TEMP_F,
    DOMAIN,
    GRILL_MODE_FAN,
    GRILL_MODE_IDLE,
    GRILL_MODE_RUN,
    GRILL_MODE_STANDBY,
    MAX_SMOKE_LEVEL,
    MAX_TEMP_F,
    MIN_SMOKE_LEVEL,
    MIN_TEMP_F,
)
from .coordinator import CampChefDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

HVAC_MODE_TO_GRILL_MODE = {
    HVACMode.OFF: GRILL_MODE_STANDBY,
    HVACMode.HEAT: GRILL_MODE_RUN,
    HVACMode.FAN_ONLY: GRILL_MODE_FAN,
    HVACMode.AUTO: GRILL_MODE_IDLE,
}

GRILL_MODE_TO_HVAC_MODE = {
    GRILL_MODE_STANDBY: HVACMode.OFF,
    GRILL_MODE_RUN: HVACMode.HEAT,
    GRILL_MODE_FAN: HVACMode.FAN_ONLY,
    GRILL_MODE_IDLE: HVACMode.AUTO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CampChef climate entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]

    entities = []

    for grill_id, coordinator in coordinators.items():
        entities.append(CampChefClimate(coordinator, grill_id))

    async_add_entities(entities)


class CampChefClimate(CoordinatorEntity, ClimateEntity):
    """CampChef grill climate entity."""

    def __init__(
        self,
        coordinator: CampChefDataUpdateCoordinator,
        grill_id: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self.grill_id = grill_id
        self._attr_name = f"{coordinator.name}"
        self._attr_unique_id = f"{grill_id}_climate"

        # Climate entity configuration
        self._attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.HEAT,
            HVACMode.FAN_ONLY,
            HVACMode.AUTO,
        ]
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE |
            ClimateEntityFeature.PRESET_MODE
        )
        self._attr_min_temp = MIN_TEMP_F
        self._attr_max_temp = MAX_TEMP_F
        self._attr_target_temperature_step = 5

        # Smoke level presets
        self._attr_preset_modes = [
            "Smoke 1", "Smoke 2", "Smoke 3", "Smoke 4", "Smoke 5",
            "Smoke 6", "Smoke 7", "Smoke 8", "Smoke 9", "Smoke 10"
        ]

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        grill_data = self.coordinator.data
        return {
            "identifiers": {(DOMAIN, self.grill_id)},
            "name": grill_data.get("name", "CampChef Grill"),
            "manufacturer": "CampChef",
            "model": grill_data.get("model", "Unknown"),
            "serial_number": grill_data.get("serialNumber"),
            "sw_version": grill_data.get("firmwareVersion"),
        }

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        grill_data = self.coordinator.data
        chamber = grill_data.get("chamber", {})
        return chamber.get("temperature")

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})
        if mode.get("type") == GRILL_MODE_RUN:
            return mode.get("temperature")
        chamber = grill_data.get("chamber", {})
        return chamber.get("target")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})
        grill_mode = mode.get("type", GRILL_MODE_STANDBY)

        return GRILL_MODE_TO_HVAC_MODE.get(grill_mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        hvac_mode = self.hvac_mode
        if hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if hvac_mode == HVACMode.AUTO:
            return HVACAction.IDLE
        return HVACAction.OFF

    @property
    def preset_mode(self) -> Optional[str]:
        """Return current preset mode (smoke level)."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})

        if mode.get("type") == GRILL_MODE_RUN:
            smoke_level = mode.get("smokeLevel", DEFAULT_SMOKE_LEVEL)
            return f"Smoke {smoke_level}"
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        grill_mode = HVAC_MODE_TO_GRILL_MODE.get(hvac_mode)
        if not grill_mode:
            _LOGGER.error("Invalid HVAC mode: %s", hvac_mode)
            return

        try:
            if grill_mode == GRILL_MODE_RUN:
                # When switching to RUN mode, use current target temp or default
                current_target = self.target_temperature or DEFAULT_TEMP_F
                current_smoke = self._get_current_smoke_level()

                await self.coordinator.async_set_grill_mode(
                    grill_mode, current_target, current_smoke
                )
            else:
                await self.coordinator.async_set_grill_mode(grill_mode)

        except Exception as err:
            _LOGGER.error("Failed to set HVAC mode: %s", err)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        target_temp = kwargs.get(ATTR_TEMPERATURE)
        if target_temp is None:
            return

        # Ensure we're in RUN mode to set temperature
        current_mode = self.hvac_mode
        if current_mode != HVACMode.HEAT:
            _LOGGER.warning(
                "Cannot set temperature when grill is not in heating mode"
            )
            return

        try:
            current_smoke = self._get_current_smoke_level()
            await self.coordinator.async_set_grill_mode(
                GRILL_MODE_RUN, int(target_temp), current_smoke
            )
        except Exception as err:
            _LOGGER.error("Failed to set temperature: %s", err)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode (smoke level)."""
        try:
            normalized_mode = preset_mode.strip().lower().replace("_", " ")
            if not normalized_mode.startswith("smoke "):
                _LOGGER.error("Invalid preset mode: %s", preset_mode)
                return

            smoke_level = int(normalized_mode.replace("smoke ", "", 1))
            if not MIN_SMOKE_LEVEL <= smoke_level <= MAX_SMOKE_LEVEL:
                _LOGGER.error("Invalid smoke level: %s", smoke_level)
                return
        except ValueError:
            _LOGGER.error("Invalid preset mode format: %s", preset_mode)
            return

        # Ensure we're in RUN mode to set smoke level
        current_mode = self.hvac_mode
        if current_mode != HVACMode.HEAT:
            _LOGGER.warning(
                "Cannot set smoke level when grill is not in heating mode"
            )
            return

        try:
            current_temp = self.target_temperature or DEFAULT_TEMP_F
            await self.coordinator.async_set_grill_mode(
                GRILL_MODE_RUN, int(current_temp), smoke_level
            )
        except Exception as err:
            _LOGGER.error("Failed to set preset mode: %s", err)

    def _get_current_smoke_level(self) -> int:
        """Get current smoke level."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})
        return mode.get("smokeLevel", DEFAULT_SMOKE_LEVEL)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})

        attributes = {
            "grill_mode": mode.get("type", "unknown"),
        }

        if mode.get("type") == GRILL_MODE_RUN:
            attributes["smoke_level"] = mode.get("smokeLevel")

        # Add pellet level if available
        pellet_level = grill_data.get("pelletLevel")
        if pellet_level is not None:
            attributes["pellet_level"] = pellet_level

        # Add connection status
        wifi_status = grill_data.get("wifiConnectionStatus")
        if wifi_status:
            attributes["wifi_status"] = wifi_status

        return attributes
