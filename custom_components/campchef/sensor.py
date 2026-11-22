"""Sensor platform for CampChef integration."""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    MODELS_WITHOUT_PELLET_SENSOR,
    UNIQUE_ID_CHAMBER_TEMP,
    UNIQUE_ID_PELLET_LEVEL,
    UNIQUE_ID_PROBE,
    UNIQUE_ID_WIFI_SIGNAL,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS: List[SensorEntityDescription] = [
    SensorEntityDescription(
        key="chamber_temperature",
        name="Chamber Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
    ),
    SensorEntityDescription(
        key="pellet_level",
        name="Pellet Level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
    ),
    SensorEntityDescription(
        key="smoke_level",
        name="Smoke Level",
        icon="mdi:smoke",
    ),
    SensorEntityDescription(
        key="wifi_signal",
        name="WiFi Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        entity_registry_enabled_default=False,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CampChef sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators = data["coordinators"]

    entities = []

    for grill_id, coordinator in coordinators.items():
        grill_data = coordinator.data

        # Add base sensors for each grill
        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                CampChefSensor(coordinator, grill_id, description)
            )

        # Always add four probe sensors (0-3); they report unavailable until data arrives
        for probe_index in range(4):
            entities.append(CampChefProbeSensor(coordinator, grill_id, probe_index))

    async_add_entities(entities)


class CampChefSensor(CoordinatorEntity, SensorEntity):
    """Base CampChef sensor entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        grill_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.grill_id = grill_id
        self._attr_name = f"{coordinator.name} {description.name}"
        self._attr_unique_id = f"{grill_id}_{description.key}"

        grill_model = (coordinator.data or {}).get("model")
        if (
            description.key == "pellet_level"
            and grill_model in MODELS_WITHOUT_PELLET_SENSOR
        ):
            self._attr_entity_registry_enabled_default = False

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
    def native_value(self) -> Optional[float]:
        """Return the state of the sensor."""
        grill_data = self.coordinator.data

        if self.entity_description.key == "chamber_temperature":
            chamber = grill_data.get("chamber", {})
            return chamber.get("temperature")

        elif self.entity_description.key == "pellet_level":
            return grill_data.get("pelletLevel")

        elif self.entity_description.key == "smoke_level":
            mode = grill_data.get("mode", {})
            return mode.get("smokeLevel")

        elif self.entity_description.key == "wifi_signal":
            return grill_data.get("wifiRssi")

        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        grill_data = self.coordinator.data
        attributes = {}

        if self.entity_description.key == "chamber_temperature":
            chamber = grill_data.get("chamber", {})
            mode = grill_data.get("mode", {})
            target_temperature = mode.get("temperature", chamber.get("target"))
            if target_temperature:
                attributes["target_temperature"] = target_temperature

        return attributes if attributes else None


class CampChefProbeSensor(CoordinatorEntity, SensorEntity):
    """CampChef probe temperature sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        grill_id: str,
        probe_index: int,
    ) -> None:
        """Initialize the probe sensor."""
        super().__init__(coordinator)
        self.grill_id = grill_id
        self.probe_index = probe_index

        # Get probe label from data
        grill_data = coordinator.data
        probes = grill_data.get("probes", [])
        probe_data = None
        for probe in probes:
            if probe.get("index") == probe_index:
                probe_data = probe
                break

        probe_label = probe_data.get("label", f"Probe {probe_index + 1}") if probe_data else f"Probe {probe_index + 1}"

        self._attr_name = f"{coordinator.name} {probe_label}"
        self._attr_unique_id = f"{grill_id}_{UNIQUE_ID_PROBE}_{probe_index}"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer-probe"

    def _get_probe_data(self) -> Optional[Dict[str, Any]]:
        """Return the current probe payload for this entity."""
        grill_data = self.coordinator.data
        probes = grill_data.get("probes", [])

        for probe in probes:
            if probe.get("index") == self.probe_index:
                return probe
        return None

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
    def native_value(self) -> Optional[float]:
        """Return the probe temperature."""
        probe = self._get_probe_data()
        if not probe:
            return None

        temperature = probe.get("temperature")
        if isinstance(temperature, (int, float)) and temperature > 0:
            return temperature
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        probe = self._get_probe_data()
        if not probe:
            return None

        attributes = {}
        if probe.get("label"):
            attributes["label"] = probe["label"]
        if self.available and probe.get("target"):
            attributes["target_temperature"] = probe["target"]

        return attributes if attributes else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False

        probe = self._get_probe_data()
        if not probe:
            return False

        temperature = probe.get("temperature")
        return isinstance(temperature, (int, float)) and temperature > 0
