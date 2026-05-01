"""实时数据管道：模拟采集 -> 入库 -> 统计 -> 报警"""
from __future__ import annotations

import random
from datetime import datetime

from flask import current_app

from extensions import db
from models import Alarm, AlarmRule, Device, DeviceSimConfig, DeviceTelemetry, EnergyDaily


def _cfg(name: str, default):
    if current_app:
        return current_app.config.get(name, default)
    return default


def _pick_rule(device: Device):
    """优先级：设备级 > 类型级 > 全局"""
    r = (
        AlarmRule.query.filter_by(enabled=True, scope_type='DEVICE', scope_key=str(device.id))
        .order_by(AlarmRule.id.desc())
        .first()
    )
    if r:
        return r
    dtype = (device.device_type or '其他').strip() or '其他'
    r = (
        AlarmRule.query.filter_by(enabled=True, scope_type='TYPE', scope_key=dtype)
        .order_by(AlarmRule.id.desc())
        .first()
    )
    if r:
        return r
    return (
        AlarmRule.query.filter_by(enabled=True, scope_type='ALL')
        .order_by(AlarmRule.id.desc())
        .first()
    )


def _create_alarm_if_needed(device: Device, alarm_type: str, message: str, value: float | None, threshold: float | None):
    """该设备只要已有任意未处理(NEW)报警，就不再新增（同一次采样里也不会叠加电压+功率等多条）。"""
    pending = (
        Alarm.query.filter(
            Alarm.device_id == device.id,
            Alarm.status == 'NEW',
        )
        .first()
    )
    if pending:
        return
    db.session.add(
        Alarm(
            device_id=device.id,
            alarm_type=alarm_type,
            message=message,
            value=value,
            threshold=threshold,
            status='NEW',
        )
    )
    # 同一轮 ingest 里后续若再触发其它类型，查询可见刚插入的 NEW
    db.session.flush()


def _apply_alarm_rules(device: Device, voltage: float, current: float, power: float):
    rule = _pick_rule(device)
    if not rule:
        return

    if rule.voltage_min is not None and voltage < rule.voltage_min:
        _create_alarm_if_needed(
            device,
            'VOLTAGE_LOW',
            f'{device.name} 电压过低：{voltage:.1f}V < {rule.voltage_min:.1f}V',
            voltage,
            rule.voltage_min,
        )
    if rule.voltage_max is not None and voltage > rule.voltage_max:
        _create_alarm_if_needed(
            device,
            'VOLTAGE_HIGH',
            f'{device.name} 电压过高：{voltage:.1f}V > {rule.voltage_max:.1f}V',
            voltage,
            rule.voltage_max,
        )
    if rule.power_max is not None and power > rule.power_max:
        _create_alarm_if_needed(
            device,
            'POWER_HIGH',
            f'{device.name} 功率过高：{power:.1f}W > {rule.power_max:.1f}W',
            power,
            rule.power_max,
        )
    if rule.current_max is not None and current > rule.current_max:
        _create_alarm_if_needed(
            device,
            'CURRENT_HIGH',
            f'{device.name} 电流过高：{current:.2f}A > {rule.current_max:.2f}A',
            current,
            rule.current_max,
        )
def ingest_telemetry(device: Device, voltage: float, current: float, power: float, ts: datetime | None = None):
    """写入采样，并更新日统计/报警"""
    ts = ts or datetime.now()

    # 以采样间隔估算本次增量电量
    sample_seconds = float(_cfg('SIM_SAMPLE_INTERVAL_SECONDS', 1))
    delta_kwh = max(power, 0.0) * sample_seconds / 3600.0 / 1000.0

    last = (
        DeviceTelemetry.query.filter_by(device_id=device.id)
        .order_by(DeviceTelemetry.ts.desc(), DeviceTelemetry.id.desc())
        .first()
    )
    prev_total = (last.energy_kwh_total or 0.0) if last else 0.0
    new_total = prev_total + delta_kwh

    row = DeviceTelemetry(
        device_id=device.id,
        ts=ts,
        voltage=round(voltage, 2),
        current=round(current, 3),
        power=round(power, 2),
        pf=None,
        energy_kwh_total=round(new_total, 6),
    )
    db.session.add(row)

    # 设备日汇总
    day = ts.date()
    daily = EnergyDaily.query.filter_by(date=day, device_id=device.id).first()
    if not daily:
        daily = EnergyDaily(date=day, device_id=device.id, energy_kwh=0.0, peak_power_w=0.0, cost_estimated=0.0)
        db.session.add(daily)
    daily.energy_kwh = float(daily.energy_kwh or 0.0) + delta_kwh
    daily.peak_power_w = max(float(daily.peak_power_w or 0.0), power)
    daily.cost_estimated = float(daily.energy_kwh or 0.0) * 0.6

    # 全局日汇总（device_id = NULL）
    total_daily = EnergyDaily.query.filter_by(date=day, device_id=None).first()
    if not total_daily:
        total_daily = EnergyDaily(date=day, device_id=None, energy_kwh=0.0, peak_power_w=0.0, cost_estimated=0.0)
        db.session.add(total_daily)
    total_daily.energy_kwh = float(total_daily.energy_kwh or 0.0) + delta_kwh
    total_daily.peak_power_w = max(float(total_daily.peak_power_w or 0.0), power)
    total_daily.cost_estimated = float(total_daily.energy_kwh or 0.0) * 0.6

    # 仅设备开启时做报警判断
    if device.is_on:
        _apply_alarm_rules(device, voltage, current, power)


def simulate_one_cycle():
    """模拟采样一轮：每台设备产出一条数据"""
    devices = Device.query.all()
    if not devices:
        return

    v_min = float(_cfg('SIM_VOLTAGE_MIN', 212.0))
    v_max = float(_cfg('SIM_VOLTAGE_MAX', 228.0))
    p_min = float(_cfg('SIM_POWER_MIN', 120.0))
    p_max = float(_cfg('SIM_POWER_MAX', 1800.0))
    standby_max = float(_cfg('SIM_STANDBY_POWER_MAX', 5.0))
    p_volt = float(_cfg('SIM_ANOMALY_PROB_VOLTAGE', 0.04))
    p_power = float(_cfg('SIM_ANOMALY_PROB_POWER', 0.04))

    per_device_cfg = {r.device_id: r for r in DeviceSimConfig.query.all()}

    for d in devices:
        dcfg = per_device_cfg.get(d.id)
        dv_min = dcfg.voltage_min if (dcfg and dcfg.voltage_min is not None) else v_min
        dv_max = dcfg.voltage_max if (dcfg and dcfg.voltage_max is not None) else v_max
        dp_min = dcfg.power_min if (dcfg and dcfg.power_min is not None) else p_min
        dp_max = dcfg.power_max if (dcfg and dcfg.power_max is not None) else p_max
        dp_volt = dcfg.anomaly_prob_voltage if (dcfg and dcfg.anomaly_prob_voltage is not None) else p_volt
        dp_power = dcfg.anomaly_prob_power if (dcfg and dcfg.anomaly_prob_power is not None) else p_power

        if d.is_on:
            # 正常运行时功率与电压
            base_power = random.uniform(dp_min, dp_max)
            voltage = random.uniform(dv_min, dv_max)

            # 小概率异常
            if random.random() < dp_volt:
                voltage = random.choice([random.uniform(190.0, 205.0), random.uniform(232.0, 245.0)])
            if random.random() < dp_power:
                base_power = random.uniform(2200.0, 3200.0)

            power = base_power
            current = power / max(voltage, 1.0)
        else:
            # 关闭设备接近待机
            voltage = random.uniform(215.0, 225.0)
            power = random.uniform(0.0, standby_max)
            current = power / max(voltage, 1.0)

        ingest_telemetry(d, voltage=voltage, current=current, power=power, ts=datetime.now())

    db.session.commit()

