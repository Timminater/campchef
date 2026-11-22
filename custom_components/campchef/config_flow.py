"""Config flow for CampChef integration."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import CampChefAPI, CampChefAuthError, CampChefConnectionError
from .const import CONF_EMAIL, CONF_PIN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): cv.string,
})

STEP_PIN_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_PIN): cv.string,
})

STEP_REAUTH_DATA_SCHEMA = vol.Schema({})


async def validate_email(hass: HomeAssistant, email: str) -> bool:
    """Validate email and send PIN."""
    session = async_get_clientsession(hass)
    api = CampChefAPI(session)

    try:
        return await api.send_email_login(email)
    except CampChefAuthError:
        raise ValueError("invalid_email")
    except CampChefConnectionError:
        raise ValueError("cannot_connect")


async def validate_pin(hass: HomeAssistant, email: str, pin: str) -> Dict[str, Any]:
    """Validate PIN and get user data."""
    session = async_get_clientsession(hass)
    api = CampChefAPI(session)

    try:
        await api.verify_pin(email, pin)
        profile = await api.get_user_profile()
        grills = await api.get_grills()

        return {
            "title": f"CampChef ({email})",
            "user_id": profile.get("_id"),
            "email": email,
            "grills_count": len(grills),
            "token": api.token,
        }
    except CampChefAuthError as err:
        if "Invalid PIN" in str(err):
            raise ValueError("invalid_pin")
        raise ValueError("invalid_auth")
    except CampChefConnectionError:
        raise ValueError("cannot_connect")


class CampChefConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CampChef."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.email: Optional[str] = None
        self.pin: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self.email = user_input[CONF_EMAIL]

            try:
                await validate_email(self.hass, self.email)
                return await self.async_step_pin()
            except ValueError as err:
                if str(err) == "invalid_email":
                    errors["base"] = "invalid_email"
                elif str(err) == "cannot_connect":
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "unknown"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_pin(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the PIN verification step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            self.pin = user_input[CONF_PIN]

            try:
                info = await validate_pin(self.hass, self.email, self.pin)
            except ValueError as err:
                if str(err) == "invalid_pin":
                    errors["base"] = "invalid_pin"
                elif str(err) == "invalid_auth":
                    errors["base"] = "invalid_auth"
                elif str(err) == "cannot_connect":
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "unknown"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if self.source == config_entries.SOURCE_REAUTH:
                    entry = self._get_reauth_entry()
                    expected_user_id = entry.unique_id or entry.data.get("user_id")
                    if not expected_user_id or info["user_id"] != expected_user_id:
                        return self.async_abort(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        entry, data_updates={"token": info["token"]}
                    )

                await self.async_set_unique_id(info["user_id"])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_EMAIL: self.email,
                        "user_id": info["user_id"],
                        "grills_count": info["grills_count"],
                        "token": info["token"],
                    }
                )

        return self.async_show_form(
            step_id="pin",
            data_schema=STEP_PIN_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"email": self.email},
        )

    async def async_step_reauth(self, entry_data: Dict[str, Any]) -> FlowResult:
        """Handle reauth flow."""
        self.email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Request a new PIN after the user confirms reauthentication."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_email(self.hass, self.email)
            except ValueError as err:
                reason = str(err)
                errors["base"] = reason if reason in ("invalid_email", "cannot_connect") else "unknown"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error requesting a CampChef PIN")
                errors["base"] = "unknown"
            else:
                return await self.async_step_pin()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"email": self.email},
        )
