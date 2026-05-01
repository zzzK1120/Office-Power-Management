"""异常报警蓝图：电压/功率异常推送与阈值"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from extensions import db
from models import Alarm, AlarmRule, Device, Office

bp = Blueprint('alarm', __name__, url_prefix='/alarm')


@bp.route('/')
def index():
    """异常报警推送页"""
    return render_template('alarm.html')


@bp.get('/api/records')
def api_records():
    rows = Alarm.query.order_by(Alarm.ts.desc(), Alarm.id.desc()).limit(100).all()
    office_ids = {r.device.office_id for r in rows if r.device and r.device.office_id is not None}
    office_map = {}
    if office_ids:
        office_map = {o.id: {'name': o.name, 'location': (o.location or '').strip()} for o in Office.query.filter(Office.id.in_(list(office_ids))).all()}

    records = []
    for r in rows:
        d = r.device
        office_info = office_map.get(d.office_id) if (d and d.office_id is not None) else None
        office_name = (office_info.get('name') if office_info else None)
        office_location = (office_info.get('location') if office_info else '')
        # 供前端直接展示：办公室位置 + 办公室名称
        if office_location and office_name:
            place = f'{office_location} · {office_name}'
        else:
            place = office_name or office_location or '未知办公室'
        data = r.to_dict()
        data['office_name'] = office_name
        data['office_location'] = office_location
        data['place'] = place
        records.append(data)
    return jsonify({'ok': True, 'records': records})


@bp.post('/api/records/<int:alarm_id>/resolve')
def api_resolve(alarm_id: int):
    row = Alarm.query.get(alarm_id)
    if not row:
        return jsonify({'ok': False, 'error': '报警不存在'}), 404
    row.status = 'RESOLVED'
    row.handled_at = datetime.now()
    row.handled_by = 'system'
    db.session.commit()
    return jsonify({'ok': True})


@bp.delete('/api/records/<int:alarm_id>')
def api_delete_record(alarm_id: int):
    """仅允许删除已处理报警"""
    row = Alarm.query.get(alarm_id)
    if not row:
        return jsonify({'ok': False, 'error': '报警不存在'}), 404
    if row.status != 'RESOLVED':
        return jsonify({'ok': False, 'error': '仅已处理报警可删除'}), 400
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


@bp.get('/api/rules')
def api_rules():
    rows = AlarmRule.query.order_by(AlarmRule.id.desc()).all()
    return jsonify({'ok': True, 'rules': [r.to_dict() for r in rows]})


@bp.get('/api/devices')
def api_devices():
    rows = Device.query.order_by(Device.id.asc()).all()
    return jsonify({
        'ok': True,
        'devices': [{'id': d.id, 'name': d.name, 'device_type': d.device_type or '其他'} for d in rows],
    })


@bp.post('/api/rules')
def api_create_rule():
    payload = request.get_json(silent=True) or {}
    scope_type = (payload.get('scope_type') or 'ALL').strip().upper()
    scope_key = (payload.get('scope_key') or '').strip()
    enabled = bool(payload.get('enabled', True))

    if scope_type not in {'ALL', 'DEVICE', 'TYPE'}:
        return jsonify({'ok': False, 'error': 'scope_type 仅支持 ALL/DEVICE/TYPE'}), 400
    if scope_type == 'DEVICE':
        try:
            device_id = int(scope_key)
        except Exception:
            return jsonify({'ok': False, 'error': 'DEVICE 规则需要有效的设备ID'}), 400
        if not Device.query.get(device_id):
            return jsonify({'ok': False, 'error': '设备不存在'}), 404
        scope_key = str(device_id)
    elif scope_type == 'TYPE':
        if not scope_key:
            return jsonify({'ok': False, 'error': 'TYPE 规则需选择设备类型'}), 400

    def to_float(name):
        v = payload.get(name)
        if v in (None, ''):
            return None
        try:
            return float(v)
        except Exception:
            raise ValueError(name)

    try:
        voltage_min = to_float('voltage_min')
        voltage_max = to_float('voltage_max')
        power_max = to_float('power_max')
        current_max = to_float('current_max')
    except ValueError as e:
        return jsonify({'ok': False, 'error': f'{e.args[0]} 格式错误'}), 400

    row = AlarmRule(
        scope_type=scope_type,
        scope_key=scope_key,
        voltage_min=voltage_min,
        voltage_max=voltage_max,
        power_max=power_max,
        current_max=current_max,
        pf_min=None,
        enabled=enabled,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'ok': True, 'rule': row.to_dict()})


@bp.delete('/api/rules/<int:rule_id>')
def api_delete_rule(rule_id: int):
    row = AlarmRule.query.get(rule_id)
    if not row:
        return jsonify({'ok': False, 'error': '规则不存在'}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


@bp.post('/api/bootstrap-default-rules')
def api_bootstrap_default_rules():
    """初始化一条全局报警规则（若不存在）"""
    exists = AlarmRule.query.filter_by(scope_type='ALL').first()
    if exists:
        return jsonify({'ok': True, 'message': '已存在全局规则'})
    row = AlarmRule(
        scope_type='ALL',
        scope_key='',
        voltage_min=210.0,
        voltage_max=230.0,
        power_max=2200.0,
        current_max=12.0,
        pf_min=None,
        enabled=True,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'ok': True, 'message': '默认规则已创建'})
