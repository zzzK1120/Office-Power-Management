"""智能关闭：基于历史「每日最晚关断」均值 ±30 分钟个性化区间，超时后结合同办公室设备联动判定忘关并自动关闭。"""
from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta

from extensions import db
from models import Alarm, Device, DeviceStateLog, Office
from services.state_log import log_device_state_change

# 学习窗口：过去若干自然日（不含今日）的每日最后关断时刻
LOOKBACK_DAYS = 14
# 最少有效样本天数才启用模型
MIN_SAMPLE_DAYS = 3
# 正常区间：均值 ± 该分钟数（与「置信区间」思路配合，以固定半宽刻画个人习惯带）
HALF_WIDTH_MINUTES = 30


def _daily_last_off_minutes(device_id: int, days_back: int) -> list[float]:
    """每个自然日最后一条「关闭」日志对应的时刻（从当日 0 点起的分钟数，可随 timedelta 跨日）。"""
    out: list[float] = []
    today = date.today()
    for d in range(1, days_back + 1):
        day = today - timedelta(days=d)
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)
        row = (
            DeviceStateLog.query.filter(
                DeviceStateLog.device_id == device_id,
                DeviceStateLog.ts >= start,
                DeviceStateLog.ts < end,
                DeviceStateLog.is_on.is_(False),
            )
            .order_by(DeviceStateLog.ts.desc(), DeviceStateLog.id.desc())
            .first()
        )
        if row and row.ts:
            t = row.ts
            out.append(t.hour * 60 + t.minute + t.second / 60.0)
    return out


def _cutoff_datetime_today(mean_minutes: float) -> datetime:
    """今日「习惯关断均值 + 半宽」对应的截止时刻（可落在次日凌晨）。"""
    base = datetime.combine(date.today(), time.min)
    return base + timedelta(minutes=mean_minutes + HALF_WIDTH_MINUTES)


def _already_smart_closed_today(device_id: int) -> bool:
    start = datetime.combine(date.today(), time.min)
    return (
        DeviceStateLog.query.filter(
            DeviceStateLog.device_id == device_id,
            DeviceStateLog.ts >= start,
            DeviceStateLog.source == 'smart_close',
            DeviceStateLog.is_on.is_(False),
        ).first()
        is not None
    )


def _other_devices_all_off(office_id: int, device_id: int) -> bool:
    """同办公室除本设备外均已关闭（无人活动迹象用此代理：无其他在用设备）。"""
    others = Device.query.filter(Device.office_id == office_id, Device.id != device_id).all()
    if not others:
        return True
    return all(not o.is_on for o in others)


def run_smart_close_checks() -> None:
    """由 APScheduler 周期性调用；对开启智能关闭的办公室逐一检查。"""
    offices = Office.query.filter_by(smart_close_enabled=True).all()
    if not offices:
        return

    now = datetime.now()
    for office in offices:
        devices = Device.query.filter_by(office_id=office.id).all()
        for d in devices:
            if not d.is_on:
                continue
            if _already_smart_closed_today(d.id):
                continue

            mins = _daily_last_off_minutes(d.id, LOOKBACK_DAYS)
            if len(mins) < MIN_SAMPLE_DAYS:
                continue

            mean_m = statistics.mean(mins)
            cutoff = _cutoff_datetime_today(mean_m)
            if now <= cutoff:
                continue

            if not _other_devices_all_off(office.id, d.id):
                continue

            log_device_state_change(d, False, source='smart_close', ts=now)
            d.is_on = False
            msg = (
                f'智能关闭：{d.name} 已超过个人习惯关断区间，同办公室其他设备均已关闭，系统已自动关闭。'
            )[:255]
            db.session.add(
                Alarm(
                    device_id=d.id,
                    alarm_type='SMART_CLOSE',
                    message=msg,
                    status='NEW',
                )
            )
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
