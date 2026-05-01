"""设备开关日志写入工具。"""
from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Device, DeviceStateLog


def log_device_state_change(device: Device, new_is_on: bool, source: str = 'manual', ts: datetime | None = None) -> bool:
    """记录一次设备状态变更（仅当状态真的变化时写入）。返回是否写入。"""
    new_is_on = bool(new_is_on)
    if bool(device.is_on) == new_is_on:
        return False
    row = DeviceStateLog(
        device_id=device.id,
        office_id=device.office_id,
        ts=ts or datetime.now(),
        is_on=new_is_on,
        source=(source or 'manual')[:32],
    )
    db.session.add(row)
    return True
