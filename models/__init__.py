"""
数据模型包：在此导入所有模型，便于 db.create_all() 能创建全部表。
"""
from extensions import db

from .device import Device
from .office import Office
from .device_group import DeviceGroup, DeviceGroupMember
from .telemetry import DeviceTelemetry
from .energy import EnergyDaily
from .schedule import Schedule
from .alarm_models import Alarm, AlarmRule
from .command import DeviceCommand
from .user import User
from .device_sim_config import DeviceSimConfig
from .device_state_log import DeviceStateLog


__all__ = [
    'db',
    'Device',
    'Office',
    'DeviceGroup',
    'DeviceGroupMember',
    'DeviceTelemetry',
    'EnergyDaily',
    'Schedule',
    'Alarm',
    'AlarmRule',
    'DeviceCommand',
    'User',
    'DeviceSimConfig',
    'DeviceStateLog',
]
