"""实时监控蓝图：设备电压、功率、用电量等"""
from datetime import date, datetime, time, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request

from extensions import db
from models import Alarm, Device, DeviceSimConfig, DeviceStateLog, DeviceTelemetry, EnergyDaily, Office
from services.office_context import get_selected_office_id
from services.scheduler_manager import reschedule_simulation_job
from services.simulation_config_store import save_from_app

bp = Blueprint('monitor', __name__, url_prefix='/monitor')


@bp.route('/')
def index():
    """设备实时监控页"""
    return render_template('monitor.html')


@bp.route('/simulation')
def simulation():
    """模拟调试页：调整采样参数/范围"""
    return render_template('simulation_debug.html')


@bp.get('/api/overview')
def api_overview():
    office_id = get_selected_office_id()
    office_row = Office.query.get(office_id)
    smart_close_enabled = bool(office_row and getattr(office_row, 'smart_close_enabled', False))
    devices = Device.query.filter_by(office_id=office_id).order_by(Device.id.desc()).all()
    result = []
    total_power = 0.0
    today_total = 0.0
    online_count = 0
    abnormal_count = 0

    latest_alarm_device_ids = {
        row[0]
        for row in Alarm.query.with_entities(Alarm.device_id).filter(Alarm.status == 'NEW').all()
        if row[0] is not None
    }

    for d in devices:
        latest = (
            DeviceTelemetry.query.filter_by(device_id=d.id)
            .order_by(DeviceTelemetry.ts.desc(), DeviceTelemetry.id.desc())
            .first()
        )
        daily = EnergyDaily.query.filter_by(date=date.today(), device_id=d.id).first()
        if d.is_on:
            online_count += 1
        day_kwh = float(daily.energy_kwh) if daily else 0.0
        today_total += day_kwh

        voltage = latest.voltage if latest else None
        power = latest.power if latest else 0.0
        total_power += float(power or 0.0)

        is_alarm = d.id in latest_alarm_device_ids
        if is_alarm:
            abnormal_count += 1

        result.append(
            {
                'id': d.id,
                'name': d.name,
                'device_type': d.device_type,
                'location': d.location,
                'is_on': d.is_on,
                'is_alarm': is_alarm,
                'voltage': voltage,
                'power': power,
                'today_kwh': round(day_kwh, 3),
            }
        )

    return jsonify(
        {
            'ok': True,
            'summary': {
                'total_power_w': round(total_power, 2),
                'today_kwh': round(today_total, 3),
                'online_count': online_count,
                'total_devices': len(devices),
                'abnormal_count': abnormal_count,
                'smart_close_enabled': smart_close_enabled,
            },
            'devices': result,
        }
    )


@bp.route('/api/smart-close', methods=['GET', 'PATCH'])
def api_smart_close():
    """当前办公室智能关闭开关（作息学习 + 区域联动，由后台定时任务执行）。"""
    office_id = get_selected_office_id()
    office_row = Office.query.get(office_id)
    if not office_row:
        return jsonify({'ok': False, 'error': '办公室不存在'}), 404

    if request.method == 'GET':
        return jsonify({'ok': True, 'enabled': bool(getattr(office_row, 'smart_close_enabled', False))})

    payload = request.get_json(silent=True) or {}
    if 'enabled' not in payload:
        return jsonify({'ok': False, 'error': 'enabled 必填'}), 400
    office_row.smart_close_enabled = bool(payload['enabled'])
    db.session.commit()
    return jsonify({'ok': True, 'enabled': office_row.smart_close_enabled})


@bp.get('/api/simulation/config')
def api_simulation_config():
    keys = [
        'SIM_SAMPLE_INTERVAL_SECONDS',
        'SIM_VOLTAGE_MIN',
        'SIM_VOLTAGE_MAX',
        'SIM_POWER_MIN',
        'SIM_POWER_MAX',
        'SIM_STANDBY_POWER_MAX',
        'SIM_ANOMALY_PROB_VOLTAGE',
        'SIM_ANOMALY_PROB_POWER',
    ]
    data = {k: current_app.config.get(k) for k in keys}
    return jsonify({'ok': True, 'config': data})


@bp.post('/api/simulation/config')
def api_simulation_config_update():
    payload = request.get_json(silent=True) or {}
    allowed = {
        'SIM_SAMPLE_INTERVAL_SECONDS': int,
        'SIM_VOLTAGE_MIN': float,
        'SIM_VOLTAGE_MAX': float,
        'SIM_POWER_MIN': float,
        'SIM_POWER_MAX': float,
        'SIM_STANDBY_POWER_MAX': float,
        'SIM_ANOMALY_PROB_VOLTAGE': float,
        'SIM_ANOMALY_PROB_POWER': float,
    }
    updated = False
    for k, caster in allowed.items():
        if k in payload:
            try:
                current_app.config[k] = caster(payload[k])
                updated = True
            except Exception:
                return jsonify({'ok': False, 'error': f'{k} 格式错误'}), 400
    if updated:
        save_from_app(current_app)
        if 'SIM_SAMPLE_INTERVAL_SECONDS' in payload:
            reschedule_simulation_job()
    return jsonify({'ok': True, 'config': {k: current_app.config.get(k) for k in allowed}})


@bp.get('/api/simulation/devices')
def api_simulation_devices():
    office_id = get_selected_office_id()
    devices = Device.query.filter_by(office_id=office_id).order_by(Device.id.asc()).all()
    cfg_map = {r.device_id: r for r in DeviceSimConfig.query.all()}
    rows = []
    for d in devices:
        cfg = cfg_map.get(d.id)
        rows.append(
            {
                'id': d.id,
                'name': d.name,
                'location': d.location,
                'is_on': d.is_on,
                'config': cfg.to_dict() if cfg else None,
            }
        )
    return jsonify({'ok': True, 'devices': rows})


@bp.post('/api/simulation/devices/<int:device_id>')
def api_simulation_device_update(device_id: int):
    d = Device.query.get(device_id)
    if not d:
        return jsonify({'ok': False, 'error': '设备不存在'}), 404
    payload = request.get_json(silent=True) or {}
    row = DeviceSimConfig.query.filter_by(device_id=device_id).first()
    if not row:
        row = DeviceSimConfig(device_id=device_id)
        db.session.add(row)

    fields = [
        'voltage_min',
        'voltage_max',
        'power_min',
        'power_max',
        'anomaly_prob_voltage',
        'anomaly_prob_power',
    ]
    for f in fields:
        if f in payload:
            v = payload[f]
            if v in (None, ''):
                setattr(row, f, None)
            else:
                try:
                    setattr(row, f, float(v))
                except Exception:
                    return jsonify({'ok': False, 'error': f'{f} 格式错误'}), 400
    db.session.commit()
    return jsonify({'ok': True, 'config': row.to_dict()})


@bp.get('/api/devices/<int:device_id>/state-logs')
def api_device_state_logs(device_id: int):
    """查询某设备某天的开关记录（精确到分钟展示由前端格式化）"""
    office_id = get_selected_office_id()
    d = Device.query.get(device_id)
    if not d or d.office_id != office_id:
        return jsonify({'ok': False, 'error': '设备不存在'}), 404

    day_str = (request.args.get('date') or '').strip()
    if not day_str:
        return jsonify({'ok': False, 'error': 'date 必填，格式 YYYY-MM-DD'}), 400
    try:
        day = date.fromisoformat(day_str[:10])
    except Exception:
        return jsonify({'ok': False, 'error': 'date 格式错误，应为 YYYY-MM-DD'}), 400
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)

    rows = (
        DeviceStateLog.query.filter(
            DeviceStateLog.device_id == device_id,
            DeviceStateLog.ts >= start,
            DeviceStateLog.ts < end,
        )
        .order_by(DeviceStateLog.ts.asc(), DeviceStateLog.id.asc())
        .all()
    )
    return jsonify({'ok': True, 'device': {'id': d.id, 'name': d.name}, 'date': day.isoformat(), 'logs': [r.to_dict() for r in rows]})
