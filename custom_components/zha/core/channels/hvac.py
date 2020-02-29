"""
HVAC channels module for Zigbee Home Automation.

For more details about this component, please refer to the documentation at
https://home-assistant.io/integrations/zha/
"""
from collections import namedtuple
import logging
from typing import Optional

import zigpy.exceptions
import zigpy.zcl.clusters.hvac as hvac
from zigpy.zcl.foundation import Status

from homeassistant.core import callback

from .. import registries
from ..const import (
    REPORT_CONFIG_MAX_INT,
    REPORT_CONFIG_MIN_INT,
    REPORT_CONFIG_OP,
    SIGNAL_ATTR_UPDATED,
)
from ..helpers import retryable_req
from .base import ZigbeeChannel

_LOGGER = logging.getLogger(__name__)

AttributeUpdateRecord = namedtuple("AttributeUpdateRecord", "attr_id, attr_name, value")
REPORT_CONFIG_CLIMATE = (REPORT_CONFIG_MIN_INT, REPORT_CONFIG_MAX_INT, 25)
REPORT_CONFIG_CLIMATE_DEMAND = (REPORT_CONFIG_MIN_INT, REPORT_CONFIG_MAX_INT, 5)
REPORT_CONFIG_CLIMATE_DISCRETE = (REPORT_CONFIG_MIN_INT, REPORT_CONFIG_MAX_INT, 1)


@registries.ZIGBEE_CHANNEL_REGISTRY.register(hvac.Dehumidification.cluster_id)
class Dehumidification(ZigbeeChannel):
    """Dehumidification channel."""

    pass


@registries.ZIGBEE_CHANNEL_REGISTRY.register(hvac.Fan.cluster_id)
class FanChannel(ZigbeeChannel):
    """Fan channel."""

    _value_attribute = 0

    REPORT_CONFIG = ({"attr": "fan_mode", "config": REPORT_CONFIG_OP},)

    async def async_set_speed(self, value) -> None:
        """Set the speed of the fan."""

        try:
            await self.cluster.write_attributes({"fan_mode": value})
        except zigpy.exceptions.DeliveryError as ex:
            self.error("Could not set speed: %s", ex)
            return

    async def async_update(self):
        """Retrieve latest state."""
        result = await self.get_attribute_value("fan_mode", from_cache=True)

        self.async_send_signal(f"{self.unique_id}_{SIGNAL_ATTR_UPDATED}", result)

    @callback
    def attribute_updated(self, attrid, value):
        """Handle attribute update from fan cluster."""
        attr_name = self.cluster.attributes.get(attrid, [attrid])[0]
        self.debug(
            "Attribute report '%s'[%s] = %s", self.cluster.name, attr_name, value
        )
        if attrid == self._value_attribute:
            self.async_send_signal(f"{self.unique_id}_{SIGNAL_ATTR_UPDATED}", value)

    async def async_initialize(self, from_cache):
        """Initialize channel."""
        await self.get_attribute_value(self._value_attribute, from_cache=from_cache)
        await super().async_initialize(from_cache)


@registries.ZIGBEE_CHANNEL_REGISTRY.register(hvac.Pump.cluster_id)
class Pump(ZigbeeChannel):
    """Pump channel."""

    pass


@registries.CLIMATE_CLUSTERS.register(hvac.Thermostat.cluster_id)
@registries.ZIGBEE_CHANNEL_REGISTRY.register(hvac.Thermostat.cluster_id)
class ThermostatChannel(ZigbeeChannel):
    """Thermostat channel."""

    def __init__(self, cluster, device):
        """Init Thermostat channel instance."""
        super().__init__(cluster, device)
        self._init_attrs = {
            "abs_min_heat_setpoint_limit": True,
            "abs_max_heat_setpoint_limit": True,
            "abs_min_cool_setpoint_limit": True,
            "abs_max_cool_setpoint_limit": True,
            "ctrl_seqe_of_oper": False,
            "local_temp": False,
            "max_cool_setpoint_limit": True,
            "max_heat_setpoint_limit": True,
            "min_cool_setpoint_limit": True,
            "min_heat_setpoint_limit": True,
            "occupancy": False,
            "occupied_cooling_setpoint": False,
            "occupied_heating_setpoint": False,
            "pi_cooling_demand": False,
            "pi_heating_demand": False,
            "running_mode": False,
            "running_state": False,
            "system_mode": False,
            "unoccupied_heating_setpoint": False,
            "unoccupied_cooling_setpoint": False,
        }
        self._abs_max_cool_setpoint_limit = 3200  # 32C
        self._abs_min_cool_setpoint_limit = 1600  # 16C
        self._ctrl_seqe_of_oper = 0xFF
        self._abs_max_heat_setpoint_limit = 3000  # 30C
        self._abs_min_heat_setpoint_limit = 700  # 7C
        self._running_mode = None
        self._max_cool_setpoint_limit = None
        self._max_heat_setpoint_limit = None
        self._min_cool_setpoint_limit = None
        self._min_heat_setpoint_limit = None
        self._local_temp = None
        self._occupancy = None
        self._occupied_cooling_setpoint = None
        self._occupied_heating_setpoint = None
        self._pi_cooling_demand = None
        self._pi_heating_demand = None
        self._running_state = None
        self._setpoint_change_source = None
        self._setpoint_change_amount = None
        self._system_mode = None
        self._unoccupied_cooling_setpoint = None
        self._unoccupied_heating_setpoint = None
        self._report_config = (
            {"attr": "local_temp", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "occupancy", "config": REPORT_CONFIG_CLIMATE_DISCRETE},
            {"attr": "occupied_cooling_setpoint", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "occupied_heating_setpoint", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "pi_cooling_demand", "config": REPORT_CONFIG_CLIMATE_DEMAND},
            {"attr": "pi_heating_demand", "config": REPORT_CONFIG_CLIMATE_DEMAND},
            {"attr": "running_mode", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "running_state", "config": REPORT_CONFIG_CLIMATE_DEMAND},
            {"attr": "system_mode", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "unoccupied_cooling_setpoint", "config": REPORT_CONFIG_CLIMATE},
            {"attr": "unoccupied_heating_setpoint", "config": REPORT_CONFIG_CLIMATE},
        )

    @property
    def abs_max_cool_setpoint_limit(self) -> int:
        """Absolute maximim cooling setpoint."""
        return self._abs_max_cool_setpoint_limit

    @property
    def abs_min_cool_setpoint_limit(self) -> int:
        """Absolute minimum cooling setpoint."""
        return self._abs_min_cool_setpoint_limit

    @property
    def abs_max_heat_setpoint_limit(self) -> int:
        """Absolute maximum heating setpoint."""
        return self._abs_max_heat_setpoint_limit

    @property
    def abs_min_heat_setpoint_limit(self) -> int:
        """Absolute minimum heating setpoint."""
        return self._abs_min_heat_setpoint_limit

    @property
    def ctrl_seqe_of_oper(self) -> int:
        """Control Sequence of operations attribute."""
        return self._ctrl_seqe_of_oper

    @property
    def max_cool_setpoint_limit(self) -> int:
        """Maximum cooling setpoint."""
        if self._max_cool_setpoint_limit is None:
            return self.abs_max_cool_setpoint_limit
        return self._max_cool_setpoint_limit

    @property
    def min_cool_setpoint_limit(self) -> int:
        """Minimum cooling setpoint."""
        if self._min_cool_setpoint_limit is None:
            return self.abs_min_cool_setpoint_limit
        return self._min_cool_setpoint_limit

    @property
    def max_heat_setpoint_limit(self) -> int:
        """Maximum heating setpoint."""
        if self._max_heat_setpoint_limit is None:
            return self.abs_max_heat_setpoint_limit
        return self._max_heat_setpoint_limit

    @property
    def min_heat_setpoint_limit(self) -> int:
        """Minimum heating setpoint."""
        if self._min_heat_setpoint_limit is None:
            return self.abs_min_heat_setpoint_limit
        return self._min_heat_setpoint_limit

    @property
    def local_temp(self) -> Optional[int]:
        """Thermostat temperature."""
        return self._local_temp

    @property
    def occupancy(self) -> Optional[int]:
        """Is occupancy detected."""
        return self._occupancy

    @property
    def occupied_cooling_setpoint(self) -> Optional[int]:
        """Temperature when room is occupied."""
        return self._occupied_cooling_setpoint

    @property
    def occupied_heating_setpoint(self) -> Optional[int]:
        """Temperature when room is occupied."""
        return self._occupied_heating_setpoint

    @property
    def pi_cooling_demand(self) -> int:
        """Cooling demand."""
        return self._pi_cooling_demand

    @property
    def pi_heating_demand(self) -> int:
        """Heating demand."""
        return self._pi_heating_demand

    @property
    def running_mode(self) -> Optional[int]:
        """Thermostat running mode."""
        return self._running_mode

    @property
    def running_state(self) -> Optional[int]:
        """Thermostat running state, state of heat, cool, fan relays."""
        return self._running_state

    @property
    def setpoint_change_amount(self) -> Optional[int]:
        """Change amount."""
        return self._setpoint_change_amount

    @property
    def setpoint_change_source(self) -> Optional[int]:
        """Change source."""
        return self._setpoint_change_source

    @property
    def system_mode(self) -> Optional[int]:
        """System mode."""
        return self._system_mode

    @property
    def unoccupied_cooling_setpoint(self) -> Optional[int]:
        """Temperature when room is not occupied."""
        return self._unoccupied_cooling_setpoint

    @property
    def unoccupied_heating_setpoint(self) -> Optional[int]:
        """Temperature when room is not occupied."""
        return self._unoccupied_heating_setpoint

    @callback
    def attribute_updated(self, attrid, value):
        """Handle attribute update cluster."""
        attr_name = self.cluster.attributes.get(attrid, [attrid])[0]
        self.debug(
            "Attribute report '%s'[%s] = %s", self.cluster.name, attr_name, value
        )
        setattr(self, "_{}".format(attr_name), value)
        self.async_send_signal(
            f"{self.unique_id}_{SIGNAL_ATTR_UPDATED}",
            AttributeUpdateRecord(attrid, attr_name, value),
        )

    async def _chunk_attr_read(self, attrs, cached=False):
        chunk, attrs = attrs[:4], attrs[4:]
        while chunk:
            res, fail = await self.cluster.read_attributes(chunk, allow_cache=cached)
            self.debug("read attributes: Success: %s. Failed: %s", res, fail)
            for attr in chunk:
                self._init_attrs.pop(attr, None)
                if attr in fail:
                    continue
                if isinstance(attr, str):
                    setattr(self, "_{}".format(attr), res[attr])
                self.async_send_signal(
                    f"{self.unique_id}_{SIGNAL_ATTR_UPDATED}",
                    AttributeUpdateRecord(None, attr, res[attr]),
                )

            chunk, attrs = attrs[:4], attrs[4:]

    @retryable_req(delays=(1, 1, 3, 6, 15, 30))
    async def async_initialize(self, from_cache):
        """Initialize channel."""
        from zigpy import types as t
        from zigpy.zcl.foundation import ReadReportingConfigRecord

        cached = [a for a, cached in self._init_attrs.items() if cached]
        uncached = [a for a, cached in self._init_attrs.items() if not cached]

        await self._chunk_attr_read(cached, cached=True)
        await self._chunk_attr_read(uncached, cached=False)
        read_attr_report_req = []
        attr_report_config_2_read = (
            0x0001,
            0x0002,
            0x0007,
            0x0008,
            0x0011,
            0x0012,
            0x0013,
            0x0014,
        )
        for attr in attr_report_config_2_read:
            rec = ReadReportingConfigRecord()
            rec.direction = t.uint8_t(0)
            rec.attrid = t.uint16_t(attr)
            read_attr_report_req.append(rec)

        await super().async_initialize(from_cache)

    async def async_set_operation_mode(self, mode) -> None:
        """Set Operation mode."""
        if not await self.write_attributes({"system_mode": mode}):
            self.debug("couldn't set '%s' operation mode", mode)
            return False

        self._system_mode = mode
        self.debug("set system to %s", mode)
        return True

    async def async_set_heating_setpoint(
        self, temperature: int, is_away: bool = False
    ) -> bool:
        """Set heating setpoint."""
        if is_away:
            data = {"unoccupied_heating_setpoint": temperature}
        else:
            data = {"occupied_heating_setpoint": temperature}
        if not await self.write_attributes(data):
            self.debug("couldn't set heating setpoint")
            return False

        if is_away:
            self._unoccupied_heating_setpoint = temperature
        else:
            self._occupied_heating_setpoint = temperature
        self.debug("set heating setpoint to %s", temperature)
        return True

    async def async_set_cooling_setpoint(
        self, temperature: int, is_away: bool = False
    ) -> bool:
        """Set cooling setpoint."""
        if is_away:
            data = {"unoccupied_cooling_setpoint": temperature}
        else:
            data = {"occupied_cooling_setpoint": temperature}
        if not await self.write_attributes(data):
            self.debug("couldn't set cooling setpoint")
            return False
        if is_away:
            self._unoccupied_cooling_setpoint = temperature
        else:
            self._occupied_cooling_setpoint = temperature
        self.debug("set cooling setpoint to %s", temperature)
        return True

    async def get_occupancy(self) -> Optional[bool]:
        """Get unreportable occupancy attribute."""
        try:
            res, fail = await self.cluster.read_attributes(["occupancy"])
            self.debug("read 'occupancy' attr, success: %s, fail: %s", res, fail)
            if "occupancy" not in res:
                return None
            self._occupancy = res["occupancy"]
            return bool(self.occupancy)
        except zigpy.exceptions.ZigbeeException as ex:
            self.debug("Couldn't read 'occupancy' attribute: %s", ex)

    async def write_attributes(self, data, **kwargs):
        """Write attributes helper."""
        try:
            res = await self.cluster.write_attributes(data, **kwargs)
        except zigpy.exceptions.ZigbeeException as exc:
            self.debug("couldn't write %s: %s", data, exc)
            return False

        self.debug("wrote %s attrs, Status: %s", data, res)
        return self.check_result(res)

    @staticmethod
    def check_result(res: list) -> bool:
        """Normalize the result."""
        if not isinstance(res, list):
            return False

        return all([record.status == Status.SUCCESS for record in res[0]])

    def log(self, level, msg, *args) -> None:
        """Log helper."""
        msg = "%s: " + msg
        args = (self.unique_id,) + args
        _LOGGER.log(level, msg, *args)


@registries.ZIGBEE_CHANNEL_REGISTRY.register(hvac.UserInterface.cluster_id)
class UserInterface(ZigbeeChannel):
    """User interface (thermostat) channel."""

    pass
