"""Button platform for CampChef integration."""
import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, GRILL_MODE_STANDBY, WIFI_STATUS_WEBSOCKET
from .coordinator import CampChefDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SHUTDOWN_BUTTON = ButtonEntityDescription(
    key="shutdown",
    name="Shutdown",
    icon="mdi:power",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CampChef button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]

    entities = [
        CampChefShutdownButton(coordinator, grill_id)
        for grill_id, coordinator in coordinators.items()
    ]

    async_add_entities(entities)


class CampChefShutdownButton(CoordinatorEntity, ButtonEntity):
    """CampChef shutdown button."""

    entity_description = SHUTDOWN_BUTTON

    def __init__(
        self,
        coordinator: CampChefDataUpdateCoordinator,
        grill_id: str,
    ) -> None:
        """Initialize the shutdown button."""
        super().__init__(coordinator)
        self.grill_id = grill_id
        self._attr_name = f"{coordinator.name} {self.entity_description.name}"
        self._attr_unique_id = f"{grill_id}_{self.entity_description.key}"

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
    def available(self) -> bool:
        """Return if the shutdown action is currently available."""
        if not self.coordinator.last_update_success:
            return False

        grill_data = self.coordinator.data
        current_session = grill_data.get("currentSession")
        mode = grill_data.get("mode", {})
        wifi_status = grill_data.get("wifiConnectionStatus")
        is_online = wifi_status == WIFI_STATUS_WEBSOCKET or grill_data.get("isOnline") is True

        if not is_online:
            return False

        # During Camp Chef shutdown countdown the grill stays online,
        # but the active session already disappears from the API.
        if current_session is None:
            return False

        return mode.get("type") != GRILL_MODE_STANDBY

    async def async_press(self) -> None:
        """Send the shutdown action."""
        try:
            await self.coordinator.async_set_grill_mode(GRILL_MODE_STANDBY)
        except Exception as err:
            _LOGGER.error("Failed to shut down grill %s: %s", self.grill_id, err)
            raise

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        grill_data = self.coordinator.data
        mode = grill_data.get("mode", {})
        attributes = {
            "action": "shutdown",
            "grill_mode": mode.get("type", "unknown"),
            "shutdown_active": grill_data.get("isOnline") is True and grill_data.get("currentSession") is None,
        }

        wifi_status = grill_data.get("wifiConnectionStatus")
        if wifi_status:
            attributes["wifi_status"] = wifi_status

        return attributes
