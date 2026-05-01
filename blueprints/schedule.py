"""定时策略蓝图：上班/下班自动开关"""
from __future__ import annotations

from datetime import date, datetime, time

from flask import Blueprint, jsonify, render_template, request

from extensions import db
from models import Device, DeviceGroup, Schedule
from services.office_context import get_selected_office_id

bp = Blueprint('schedule', __name__, url_prefix='/schedule')


@bp.route('/')
def index():
    """简单定时策略页"""
    return render_template('schedule.html')


def _json_error(message: str, status_code: int = 400):
    return jsonify({'ok': False, 'error': message}), status_code


@bp.get('/api/options')
def api_schedule_options():
    """提供策略可选的设备/分组列表"""
    office_id = get_selected_office_id()
    devices = Device.query.filter_by(office_id=office_id).order_by(Device.id.desc()).all()
    groups = DeviceGroup.query.filter_by(office_id=office_id).order_by(DeviceGroup.id.desc()).all()
    return jsonify(
        {
            'ok': True,
            'devices': [{'id': d.id, 'name': d.name, 'location': d.location} for d in devices],
            'groups': [{'id': g.id, 'name': g.name} for g in groups],
        }
    )


@bp.get('/api/schedules')
def api_schedules_list():
    office_id = get_selected_office_id()
    rows = Schedule.query.filter_by(office_id=office_id).order_by(Schedule.time_of_day, Schedule.id).all()
    data = []
    device_map = {d.id: d for d in Device.query.filter_by(office_id=office_id).all()}
    group_map = {g.id: g for g in DeviceGroup.query.filter_by(office_id=office_id).all()}

    for s in rows:
        target_name = ''
        if s.target_type == 'DEVICE':
            d = device_map.get(s.target_id)
            if d:
                target_name = f'设备「{d.name}」'
        elif s.target_type == 'GROUP':
            g = group_map.get(s.target_id)
            if g:
                target_name = f'分组「{g.name}」'
        elif s.target_type == 'COLLECTION':
            target_name = f'办公室范围「{s.name}」'

        repeat_label = {
            'ONCE': '仅一次',
            'EVERYDAY': '每天',
            'WEEKDAY': '仅工作日',
            'WEEKEND': '仅周末',
            'CUSTOM': '自定义',
        }.get(s.repeat_type, s.repeat_type)

        data.append(
            {
                'id': s.id,
                'name': s.name,
                'target_type': s.target_type,
                'target_id': s.target_id,
                'target_name': target_name,
                'action': s.action,
                'time_of_day': s.time_of_day.strftime('%H:%M') if s.time_of_day else None,
                'run_date': s.run_date.isoformat() if s.run_date else None,
                'repeat_type': s.repeat_type,
                'repeat_label': repeat_label,
                'repeat_days': s.repeat_days,
                'enabled': s.enabled,
            }
        )
    return jsonify({'ok': True, 'schedules': data})


def _parse_time(value: str) -> time:
    try:
        return datetime.strptime(value, '%H:%M').time()
    except Exception:
        raise ValueError('时间格式应为 HH:MM')


@bp.post('/api/schedules')
def api_schedules_create():
    office_id = get_selected_office_id()
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    time_str = (payload.get('time') or '').strip()
    action = (payload.get('action') or '').strip().upper()
    target_type = (payload.get('target_type') or '').strip().upper()
    target_id = payload.get('target_id')
    repeat_type = (payload.get('repeat_type') or '').strip().upper() or 'ONCE'
    repeat_days = (payload.get('repeat_days') or '').strip()
    run_date_text = (payload.get('run_date') or '').strip()

    if not name:
        return _json_error('策略名称不能为空')
    if not time_str:
        return _json_error('执行时间不能为空')
    if action not in {'ON', 'OFF'}:
        return _json_error('动作必须是 ON 或 OFF')
    if target_type not in {'DEVICE', 'GROUP'}:
        return _json_error('作用对象类型必须是 DEVICE 或 GROUP')
    try:
        target_id = int(target_id)
    except Exception:
        return _json_error('target_id 无效')

    if target_type == 'DEVICE':
        d = Device.query.get(target_id)
        if not d or d.office_id != office_id:
            return _json_error('设备不存在')
    else:
        g = DeviceGroup.query.get(target_id)
        if not g or g.office_id != office_id:
            return _json_error('分组不存在')

    try:
        t = _parse_time(time_str)
    except ValueError as e:
        return _json_error(str(e))

    run_date = None
    if repeat_type == 'ONCE':
        if not run_date_text:
            return _json_error('单次策略必须提供执行日期')
        try:
            run_date = date.fromisoformat(run_date_text)
        except ValueError:
            return _json_error('执行日期格式应为 YYYY-MM-DD')

    s = Schedule(
        office_id=office_id,
        name=name,
        target_type=target_type,
        target_id=target_id,
        action=action,
        time_of_day=t,
        repeat_type=repeat_type,
        repeat_days=repeat_days,
        run_date=run_date,
        enabled=True,
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({'ok': True, 'id': s.id})


@bp.post('/api/schedules/<int:schedule_id>/toggle')
def api_schedules_toggle(schedule_id: int):
    office_id = get_selected_office_id()
    s = Schedule.query.get(schedule_id)
    if not s or s.office_id != office_id:
        return _json_error('策略不存在', 404)
    s.enabled = not s.enabled
    db.session.commit()
    return jsonify({'ok': True, 'enabled': s.enabled})


@bp.delete('/api/schedules/<int:schedule_id>')
def api_schedules_delete(schedule_id: int):
    office_id = get_selected_office_id()
    s = Schedule.query.get(schedule_id)
    if not s or s.office_id != office_id:
        return _json_error('策略不存在', 404)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'ok': True})
