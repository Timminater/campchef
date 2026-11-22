"""The CampChef integration."""
import asyncio
import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .api import CampChefAPI, CampChefAuthError, CampChefConnectionError
from .const import CONF_EMAIL, DOMAIN, MODELS_WITHOUT_PELLET_SENSOR, PLATFORMS
from .coordinator import CampChefDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.CLIMATE,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CampChef from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = CampChefAPI(session)

    # Get stored token from config entry
    email = entry.data[CONF_EMAIL]
    stored_token = entry.data.get("token")

    if stored_token:
        api.token = stored_token
        _LOGGER.debug("Using stored token for %s", email)

        # Test if token is still valid
        try:
            profile = await api.get_user_profile()
            _LOGGER.debug("Token validation successful for %s", email)
        except CampChefAuthError:
            _LOGGER.info("Stored token expired for %s, triggering reauth", email)
            entry.async_start_reauth(hass)
            return False
        except CampChefConnectionError as err:
            _LOGGER.error("Connection error: %s", err)
            raise ConfigEntryNotReady from err
    else:
        _LOGGER.info("No stored token for %s, triggering reauth", email)
        entry.async_start_reauth(hass)
        return False

    # Get grills data
    try:
        grills = await api.get_grills()
    except (CampChefAuthError, CampChefConnectionError) as err:
        _LOGGER.error("Failed to get grills: %s", err)
        raise ConfigEntryNotReady from err

    if not grills:
        _LOGGER.warning("No grills found for user %s", email)
        return False

    # Create coordinators for each grill
    coordinators = {}
    for grill in grills:
        grill_id = grill["_id"]
        coordinator = CampChefDataUpdateCoordinator(
            hass, api, grill_id, grill["name"]
        )

        # Fetch initial data
        await coordinator.async_config_entry_first_refresh()
        coordinators[grill_id] = coordinator

    # Store coordinators and API
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinators": coordinators,
    }

    await _async_remove_legacy_switch_entities(hass, entry)
    await _async_disable_unsupported_entities(hass, entry, coordinators)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up coordinators and close WebSocket connections
        data = hass.data[DOMAIN][entry.entry_id]
        coordinators = data["coordinators"]

        # Close all WebSocket connections
        for coordinator in coordinators.values():
            if hasattr(coordinator, 'websocket') and coordinator.websocket:
                await coordinator.websocket.disconnect()

        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _async_remove_legacy_switch_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove switch entities that are now exposed via the climate entity."""
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity_entry.unique_id and entity_entry.unique_id.endswith(("_fan_mode", "_idle_mode")):
            entity_registry.async_remove(entity_entry.entity_id)


async def _async_disable_unsupported_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinators: Dict[str, CampChefDataUpdateCoordinator],
) -> None:
    """Disable entities that are not supported by the current grill model."""
    entity_registry = er.async_get(hass)
    no_pellet_sensor_grills = {
        grill_id
        for grill_id, coordinator in coordinators.items()
        if (coordinator.data or {}).get("model") in MODELS_WITHOUT_PELLET_SENSOR
    }

    if not no_pellet_sensor_grills:
        return

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not entity_entry.unique_id:
            continue
        if not entity_entry.unique_id.endswith("_pellet_level"):
            continue

        grill_id = entity_entry.unique_id.removesuffix("_pellet_level")
        if grill_id not in no_pellet_sensor_grills:
            continue

        if entity_entry.disabled_by is None:
            entity_registry.async_update_entity(
                entity_entry.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )
