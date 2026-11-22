"""Binary sensor platform for CampChef integration."""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    FAULT_NONE,
    UNIQUE_ID_CONNECTION_STATUS,
    UNIQUE_ID_FAULT_STATUS,
    WIFI_STATUS_WEBSOCKET,
)

_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_DESCRIPTIONS: List[BinarySensorEntityDescription] = [
    BinarySensorEntityDescription(
        key="connection_status",
        name="Connection Status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wifi",
    ),
    BinarySensorEntityDescription(
        key="fault_status",
        name="Fault Status",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CampChef binary sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]

    entities = []

    for grill_id, coordinator in coordinators.items():
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(
                CampChefBinarySensor(coordinator, grill_id, description)
            )

    async_add_entities(entities)


class CampChefBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """CampChef binary sensor entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        grill_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.grill_id = grill_id
        self._attr_name = f"{coordinator.name} {description.name}"
        self._attr_unique_id = f"{grill_id}_{description.key}"

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
    def is_on(self) -> Optional[bool]:
        """Return true if the binary sensor is on."""
        grill_data = self.coordinator.data

        if self.entity_description.key == "connection_status":
            wifi_status = grill_data.get("wifiConnectionStatus")
            return wifi_status == WIFI_STATUS_WEBSOCKET

        elif self.entity_description.key == "fault_status":
            fault = grill_data.get("lastFault")
            if isinstance(fault, dict):
                fault_code = fault.get("code", FAULT_NONE)
            else:
                fault_code = fault or FAULT_NONE
            return fault_code != FAULT_NONE

        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        grill_data = self.coordinator.data
        attributes = {}

        if self.entity_description.key == "connection_status":
            wifi_status = grill_data.get("wifiConnectionStatus")
            if wifi_status:
                attributes["wifi_status"] = wifi_status

            wifi_ssid = grill_data.get("wifiSsid")
            if wifi_ssid:
                attributes["wifi_ssid"] = wifi_ssid

            wifi_rssi = grill_data.get("wifiRssi")
            if wifi_rssi is not None:
                attributes["wifi_rssi"] = wifi_rssi

        elif self.entity_description.key == "fault_status":
            fault = grill_data.get("lastFault")
            if isinstance(fault, dict):
                attributes["fault_code"] = fault.get("code", FAULT_NONE)
                attributes["fault_id"] = fault.get("id")
                if fault.get("timestamp"):
                    attributes["fault_timestamp"] = fault["timestamp"]
            elif fault:
                attributes["fault_code"] = fault

        return attributes if attributes else None