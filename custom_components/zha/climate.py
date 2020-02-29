"""
Climate on Zigbee Home Automation networks.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/zha.climate/
"""
from datetime import timedelta
import enum
import functools
import logging
import time
from typing import List, Optional

from homeassistant.components.climate import ClimateDevice
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    CURRENT_HVAC_COOL,
    CURRENT_HVAC_FAN,
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF,
    DOMAIN,
    FAN_OFF,
    FAN_ON,
    HVAC_MODE_COOL,
    HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY,
    HVAC_MODE_HEAT,
    HVAC_MODE_HEAT_COOL,
    HVAC_MODE_OFF,
    PRESET_AWAY,
    PRESET_NONE,
    SUPPORT_FAN_MODE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_TARGET_TEMPERATURE_RANGE,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, TEMP_CELSIUS
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.temperature import convert_temperature

from .core import discovery
from .core.const import (
    CHANNEL_FAN,
    CHANNEL_THERMOSTAT,
    DATA_ZHA,
    DATA_ZHA_DISPATCHERS,
    SIGNAL_ADD_ENTITIES,
    SIGNAL_ATTR_UPDATED,
)
from .core.registries import ZHA_ENTITIES
from .entity import ZhaEntity

DEPENDENCIES = ["zha"]

ATTR_SYS_MODE = "system_mode"
ATTR_RUNNING_MODE = "running_mode"
ATTR_SETPT_CHANGE_SRC = "setpoint_change_source"
ATTR_SETPT_CHANGE_AMT = "setpoint_change_amount"
ATTR_OCCUPANCY = "occupancy"
ATTR_PI_COOLING_DEMAND = "pi_cooling_demand"
ATTR_PI_HEATING_DEMAND = "pi_heating_demand"
ATTR_OCCP_COOL_SETPT = "occupied_cooling_setpoint"
ATTR_OCCP_HEAT_SETPT = "occupied_heating_setpoint"
ATTR_UNOCCP_HEAT_SETPT = "unoccupied_heating_setpoint"
ATTR_UNOCCP_COOL_SETPT = "unoccupied_cooling_setpoint"


STRICT_MATCH = functools.partial(ZHA_ENTITIES.strict_match, DOMAIN)
RUNNING_MODE = {0x00: HVAC_MODE_OFF, 0x03: HVAC_MODE_COOL, 0x04: HVAC_MODE_HEAT}


class RunningState(enum.IntFlag):
    """ZCL Running state enum."""

    HEAT = 0x0001
    COOL = 0x0002
    FAN = 0x0004
    HEAT_STAGE_2 = 0x0008
    COOL_STAGE_2 = 0x0010
    FAN_STAGE_2 = 0x0020
    FAN_STAGE_3 = 0x0040


SEQ_OF_OPERATION = {
    0x00: [HVAC_MODE_OFF, HVAC_MODE_COOL],  # cooling only
    0x01: [HVAC_MODE_OFF, HVAC_MODE_COOL],  # cooling with reheat
    0x02: [HVAC_MODE_OFF, HVAC_MODE_HEAT],  # heating only
    0x03: [HVAC_MODE_OFF, HVAC_MODE_HEAT],  # heating with reheat
    # cooling and heating 4-pipes
    0x04: [HVAC_MODE_OFF, HVAC_MODE_HEAT_COOL, HVAC_MODE_COOL, HVAC_MODE_HEAT],
    # cooling and heating 4-pipes
    0x05: [HVAC_MODE_OFF, HVAC_MODE_HEAT_COOL, HVAC_MODE_COOL, HVAC_MODE_HEAT],
}


class SystemMode(enum.IntEnum):
    """ZCL System Mode attribute enum."""

    OFF = 0x00
    HEAT_COOL = 0x01
    COOL = 0x03
    HEAT = 0x04
    AUX_HEAT = 0x05
    PRE_COOL = 0x06
    FAN_ONLY = 0x07
    DRY = 0x08
    SLEEP = 0x09


HVAC_MODE_2_SYSTEM = {
    HVAC_MODE_OFF: SystemMode.OFF,
    HVAC_MODE_HEAT_COOL: SystemMode.HEAT_COOL,
    HVAC_MODE_COOL: SystemMode.COOL,
    HVAC_MODE_HEAT: SystemMode.HEAT,
    HVAC_MODE_FAN_ONLY: SystemMode.FAN_ONLY,
    HVAC_MODE_DRY: SystemMode.DRY,
}

SYSTEM_MODE_2_HVAC = {
    SystemMode.OFF: HVAC_MODE_OFF,
    SystemMode.HEAT_COOL: HVAC_MODE_HEAT_COOL,
    SystemMode.COOL: HVAC_MODE_COOL,
    SystemMode.HEAT: HVAC_MODE_HEAT,
    SystemMode.AUX_HEAT: HVAC_MODE_HEAT,
    SystemMode.PRE_COOL: HVAC_MODE_COOL,  # this is 'precooling'. is it the same?
    SystemMode.FAN_ONLY: HVAC_MODE_FAN_ONLY,
    SystemMode.DRY: HVAC_MODE_DRY,
    SystemMode.SLEEP: HVAC_MODE_OFF,
}

ZCL_TEMP = 100
SECS_2000_01_01 = 946_702_800

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Zigbee Home Automation sensor from config entry."""
    entities_to_create = hass.data[DATA_ZHA][DOMAIN] = []
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(
            discovery.async_add_entities, async_add_entities, entities_to_create
        ),
    )
    hass.data[DATA_ZHA][DATA_ZHA_DISPATCHERS].append(unsub)


@STRICT_MATCH(channel_names=CHANNEL_THERMOSTAT, aux_channels=CHANNEL_FAN)
class Thermostat(ZhaEntity, ClimateDevice):
    """Representation of a ZHA Thermostat device."""

    DEFAULT_MAX_TEMP = 35
    DEFAULT_MIN_TEMP = 7

    _domain = DOMAIN
    value_attribute = 0x0000

    def __init__(self, unique_id, zha_device, channels, **kwargs):
        """Initialize ZHA Thermostat instance."""
        super().__init__(unique_id, zha_device, channels, **kwargs)
        self._thrm = self.cluster_channels.get(CHANNEL_THERMOSTAT)
        self._preset = PRESET_NONE
        self._presets = None
        self._supported_flags = SUPPORT_TARGET_TEMPERATURE
        self._fan = self.cluster_channels.get(CHANNEL_FAN)
        self._target_temp = None
        self._target_range = (None, None)

    @property
    def current_temperature(self):
        """Return the current temperature."""
        if self._thrm.local_temp is None:
            return None
        return self._thrm.local_temp / ZCL_TEMP

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        data = {}
        if self.hvac_mode:
            data[ATTR_SYS_MODE] = "[{}]/{}".format(
                self._thrm.system_mode,
                SYSTEM_MODE_2_HVAC.get(self._thrm.system_mode, "unknown"),
            )
        if self._thrm.setpoint_change_source:
            data[ATTR_SETPT_CHANGE_SRC] = self._thrm.setpoint_change_source
        if self._thrm.setpoint_change_amount:
            data[ATTR_SETPT_CHANGE_AMT] = self._thrm.setpoint_change_amount
        if self._thrm.occupancy:
            data[ATTR_OCCUPANCY] = self._thrm.occupancy
        if self._thrm.occupied_cooling_setpoint:
            data[ATTR_OCCP_COOL_SETPT] = self._thrm.occupied_cooling_setpoint
        if self._thrm.occupied_heating_setpoint:
            data[ATTR_OCCP_HEAT_SETPT] = self._thrm.occupied_heating_setpoint

        unoccupied_cooling_setpoint = self._thrm.unoccupied_cooling_setpoint
        if unoccupied_cooling_setpoint:
            data[ATTR_UNOCCP_HEAT_SETPT] = unoccupied_cooling_setpoint

        unoccupied_heating_setpoint = self._thrm.unoccupied_heating_setpoint
        if unoccupied_heating_setpoint:
            data[ATTR_UNOCCP_COOL_SETPT] = unoccupied_heating_setpoint
        return data

    @property
    def fan(self):
        """Fan channel."""
        return self._fan

    @property
    def fan_mode(self) -> Optional[str]:
        """Return current FAN mode."""
        return None

    @property
    def fan_modes(self) -> Optional[List[str]]:
        """Return supported FAN modes."""
        return [FAN_OFF, FAN_ON]

    @property
    def hvac_action(self) -> Optional[str]:
        """Return the current HVAC action."""
        if (
            self._thrm.pi_heating_demand is None
            and self._thrm.pi_cooling_demand is None
        ):
            self.debug("Running mode: %s", self._thrm.running_mode)
            self.debug("Running state: %s", self._thrm.running_state)
            rs = self._thrm.running_state
            if rs is None:
                return None
            if rs & (RunningState.HEAT | RunningState.HEAT_STAGE_2):
                return CURRENT_HVAC_HEAT
            if rs & (RunningState.COOL | RunningState.COOL_STAGE_2):
                return CURRENT_HVAC_COOL
            if rs & (
                RunningState.FAN | RunningState.FAN_STAGE_2 | RunningState.FAN_STAGE_3
            ):
                return CURRENT_HVAC_FAN
        else:
            heating_demand = self._thrm.pi_heating_demand
            if heating_demand is not None and heating_demand > 0:
                return CURRENT_HVAC_HEAT
            cooling_demand = self._thrm.pi_cooling_demand
            if cooling_demand is not None and cooling_demand > 0:
                return CURRENT_HVAC_COOL
        if self.hvac_mode != HVAC_MODE_OFF:
            return CURRENT_HVAC_IDLE
        return CURRENT_HVAC_OFF

    @property
    def hvac_mode(self) -> str:
        """Return current HVAC operation mode."""
        mode = SYSTEM_MODE_2_HVAC.get(self._thrm.system_mode)
        if mode is None:
            self.error(
                "can't map 'system_mode: %s' to a HVAC mode", self._thrm.system_mode
            )
        return mode

    @property
    def hvac_modes(self) -> List[str]:
        """Return the list of available HVAC operation modes."""
        modes = SEQ_OF_OPERATION.get(self._thrm.ctrl_seqe_of_oper, [HVAC_MODE_OFF])
        if self.fan is not None:
            modes.append(HVAC_MODE_FAN_ONLY)
        return modes

    @property
    def is_aux_heat(self) -> Optional[bool]:
        """Return True if aux heat is on."""
        return self._thrm.system_mode == SystemMode.AUX_HEAT

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION_HALVES

    @property
    def preset_mode(self) -> Optional[str]:
        """Return current preset mode."""
        return self._preset

    @property
    def preset_modes(self) -> Optional[List[str]]:
        """Return supported preset modes."""
        return self._presets

    @property
    def supported_features(self):
        """Return the list of supported features."""
        features = self._supported_flags
        if HVAC_MODE_HEAT_COOL in self.hvac_modes:
            features |= SUPPORT_TARGET_TEMPERATURE_RANGE
        if self._fan is not None:
            self._supported_flags |= SUPPORT_FAN_MODE
        return features

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        temp = None
        if self.hvac_mode == HVAC_MODE_COOL:
            if self.preset_mode == PRESET_AWAY:
                temp = self._thrm.unoccupied_cooling_setpoint
            else:
                temp = self._thrm.occupied_cooling_setpoint
        elif self.hvac_mode == HVAC_MODE_HEAT:
            if self.preset_mode == PRESET_AWAY:
                temp = self._thrm.unoccupied_heating_setpoint
            else:
                temp = self._thrm.occupied_heating_setpoint
        if temp is None:
            return temp
        return round(temp / ZCL_TEMP, 1)

    @property
    def target_temperature_high(self):
        """Return the upper bound temperature we try to reach."""
        if self.hvac_mode != HVAC_MODE_HEAT_COOL:
            return None
        if self.preset_mode == PRESET_AWAY:
            temp = self._thrm.unoccupied_cooling_setpoint
        else:
            temp = self._thrm.occupied_cooling_setpoint

        if temp is None:
            return temp

        return round(temp / ZCL_TEMP, 1)

    @property
    def target_temperature_low(self):
        """Return the lower bound temperature we try to reach."""
        if self.hvac_mode != HVAC_MODE_HEAT_COOL:
            return None
        if self.preset_mode == PRESET_AWAY:
            temp = self._thrm.unoccupied_heating_setpoint
        else:
            temp = self._thrm.occupied_heating_setpoint

        if temp is None:
            return temp
        return round(temp / ZCL_TEMP, 1)

    @property
    def temperature_unit(self):
        """Return the unit of measurement used by the platform."""
        return TEMP_CELSIUS

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        temps = []
        if HVAC_MODE_HEAT in self.hvac_modes:
            temps.append(self._thrm.max_heat_setpoint_limit)
        if HVAC_MODE_COOL in self.hvac_modes:
            temps.append(self._thrm.max_cool_setpoint_limit)

        if not temps:
            return self.DEFAULT_MAX_TEMP
        return round(max(temps) / ZCL_TEMP, 1)

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        temps = []
        if HVAC_MODE_HEAT in self.hvac_modes:
            temps.append(self._thrm.min_heat_setpoint_limit)
        if HVAC_MODE_COOL in self.hvac_modes:
            temps.append(self._thrm.min_cool_setpoint_limit)

        if not temps:
            return self.DEFAULT_MIN_TEMP
        return round(min(temps) / ZCL_TEMP, 1)

    async def async_added_to_hass(self):
        """Run when about to be added to hass."""
        await super().async_added_to_hass()
        await self.async_accept_signal(
            self._thrm, SIGNAL_ATTR_UPDATED, self.async_attribute_updated
        )

    async def async_attribute_updated(self, record):
        """Handle attribute update from device."""
        if (
            record.attr_name in (ATTR_OCCP_COOL_SETPT, ATTR_OCCP_HEAT_SETPT)
            and self.preset_mode == PRESET_AWAY
        ):
            # occupancy attribute is an unreportable attribute, but if we get
            # an attribute update for an "occupied" setpoint, there's a chance
            # occupancy has changed
            occupancy = await self._thrm.get_occupancy()
            if occupancy is True:
                self._preset = None

        self.async_schedule_update_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target operation mode."""
        if hvac_mode not in self.hvac_modes:
            self.warn(
                "can't set '%s' mode. Supported modes are: %s",
                hvac_mode,
                self.hvac_modes,
            )
            return

        system_mode = HVAC_MODE_2_SYSTEM.get(hvac_mode)
        if system_mode is None:
            self.error("Couldn't map operation %s to system_mode", hvac_mode)
            return

        if await self._thrm.async_set_operation_mode(system_mode):
            self.async_schedule_update_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in self.preset_modes:
            self.debug("preset mode '%s' is not supported", preset_mode)
            return

        if self.preset_mode not in (preset_mode, PRESET_NONE):
            if not await self.async_preset_handler(self.preset_mode, enable=False):
                self.debug("Couldn't turn off '%s' preset", self.preset_mode)
                return

        if preset_mode != PRESET_NONE:
            if not await self.async_preset_handler(preset_mode, enable=True):
                self.debug("Couldn't turn on '%s' preset", preset_mode)
                return
        self._preset = preset_mode
        self.async_schedule_update_ha_state()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        low_temp = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high_temp = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        temp = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)

        self.debug("target temperature %s", temp)
        self.debug("low temperature %s", low_temp)
        self.debug("high temperature %s", high_temp)
        self.debug("operation mode: %s", hvac_mode)

        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)

        thrm = self._thrm
        if self.hvac_mode == HVAC_MODE_HEAT_COOL:
            success = True
            if low_temp is not None:
                low_temp = int(low_temp * ZCL_TEMP)
                success = success and await thrm.async_set_heating_setpoint(
                    low_temp, self.preset_mode == PRESET_AWAY
                )
            if high_temp is not None:
                high_temp = int(high_temp * ZCL_TEMP)
                success = success and await thrm.async_set_cooling_setpoint(
                    high_temp, self.preset_mode == PRESET_AWAY
                )
        elif temp is not None:
            temp = int(temp * ZCL_TEMP)
            success = True
            if self.hvac_mode == HVAC_MODE_COOL:
                success = success and await thrm.async_set_cooling_setpoint(
                    temp, self.preset_mode == PRESET_AWAY
                )
            elif self.hvac_mode == HVAC_MODE_HEAT:
                success = success and await thrm.async_set_heating_setpoint(
                    temp, self.preset_mode == PRESET_AWAY
                )
            else:
                self.debug("Not setting temperature for '%s' mode", self.hvac_mode)
                success = False
            if success:
                self._target_temp = temp / ZCL_TEMP
        else:
            success = False
            self.debug(
                "not setting temperature %s for '%s' mode", kwargs, self.hvac_mode
            )
        if success:
            self.async_schedule_update_ha_state()

    async def async_turn_aux_heat_off(self) -> None:
        """Turn off aux heater."""
        if await self._thrm.async_set_operation_mode(SystemMode.HEAT):
            self.async_schedule_update_ha_state()

    async def async_turn_aux_heat_on(self) -> None:
        """Turn on aux heater."""
        if await self._thrm.async_set_operation_mode(SystemMode.AUX_HEAT):
            self.async_schedule_update_ha_state()

    async def async_update_outdoor_temperature(self, temperature):
        """Update outdoor temperature display."""
        pass

    async def async_preset_handler(self, preset: str, enable: bool = False) -> bool:
        """Set the preset mode via handler."""
        handler = getattr(self, f"async_preset_handler_{preset}", None)
        if handler is None:
            self.warn("No '%s' preset handler", preset)
            return

        return await handler(enable)


@STRICT_MATCH(channel_names=CHANNEL_THERMOSTAT, manufacturers="Sinope Technologies")
class SinopeTechnologiesThermostat(Thermostat):
    """Sinope Technologies Thermostat."""

    manufacturer = 0x119C
    update_time_interval = timedelta(minutes=15)

    def __init__(self, unique_id, zha_device, channels, **kwargs):
        """Initialize ZHA Thermostat instance."""
        super().__init__(unique_id, zha_device, channels, **kwargs)
        self._presets = [PRESET_AWAY, PRESET_NONE]
        self._supported_flags |= SUPPORT_PRESET_MODE

    async def _async_update_time(self, timestamp=None):
        """Update thermostat's time display."""

        secs_since_2k = int(time.mktime(time.localtime()) - SECS_2000_01_01)
        self.debug("Updating time: %s", secs_since_2k)
        cluster = self.endpoint.sinope_manufacturer_specific
        res = await cluster.write_attributes(
            {"secs_since_2k": secs_since_2k}, manufacturer=self.manufacturer
        )
        self.debug("Write Attr: %s", res)

    async def async_added_to_hass(self):
        """Run when about to be added to Hass."""
        await super().async_added_to_hass()
        # async_track_time_interval(self.hass, self._async_update_time,
        #                          self.update_time_interval)
        # async_call_later(self.hass, randint(30, 45), self._async_update_time)

    async def async_preset_handler_away(self, is_away: bool = False) -> bool:
        """Set occupancy."""
        mfg_code = self._zha_device.manufacturer_code
        res = await self._thrm.write_attributes(
            {"set_occupancy": 0 if is_away else 1}, manufacturer=mfg_code
        )

        self.debug("set occupancy to %s. Status: %s", 0 if is_away else 1, res)
        return res

    async def async_update_outdoor_temperature(self, temperature):
        """Update Outdoor temperature display service call."""
        outdoor_temp = convert_temperature(
            temperature, self.hass.config.units.temperature_unit, TEMP_CELSIUS
        )
        outdoor_temp = int(outdoor_temp * ZCL_TEMP)
        self.debug("Updating outdoor temp to %s", outdoor_temp)
        cluster = self.endpoint.sinope_manufacturer_specific
        res = await cluster.write_attributes(
            {"outdoor_temp": outdoor_temp}, manufacturer=self.manufacturer
        )
        self.debug("Write Attr: %s", res)
