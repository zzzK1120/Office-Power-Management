"""远程控制蓝图：单个/分组开关控制 + CRUD/API"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from extensions import db
from models import Device, DeviceGroup, DeviceGroupMember
from services.office_context import get_selected_office_id
from services.state_log import log_device_state_change

bp = Blueprint('control', __name__, url_prefix='/control')


@bp.route('/')
def index():
    """远程开关控制页"""
    return render_template('control.html')


def _json_error(message: str, status_code: int = 400):
    return jsonify({'ok': False, 'error': message}), status_code


@bp.get('/api/groups')
def api_groups_list():
    office_id = get_selected_office_id()
    groups = DeviceGroup.query.filter_by(office_id=office_id).order_by(DeviceGroup.id.desc()).all()
    data = []
    for g in groups:
        device_count = int(
            (
                db.session.query(db.func.count(DeviceGroupMember.id))
                .filter(DeviceGroupMember.group_id == g.id)
                .scalar()
                or 0
            )
        )
        on_count = int(
            (
                db.session.query(db.func.count(Device.id))
                .join(DeviceGroupMember, DeviceGroupMember.device_id == Device.id)
                .filter(DeviceGroupMember.group_id == g.id, Device.is_on.is_(True))
                .scalar()
                or 0
            )
        )
        data.append(
            {
                'id': g.id,
                'name': g.name,
                'description': g.description,
                'device_count': device_count,
                'on_count': on_count,
                # 分组开关状态：组内设备全部开启才算“开”
                'is_on': (device_count > 0 and on_count == device_count),
            }
        )
    return jsonify({'ok': True, 'groups': data})


@bp.post('/api/groups')
def api_groups_create():
    office_id = get_selected_office_id()
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    description = (payload.get('description') or '').strip()

    if not name:
        return _json_error('分组名称不能为空')

    exists = DeviceGroup.query.filter_by(name=name, office_id=office_id).first()
    if exists:
        return _json_error('分组名称已存在')

    g = DeviceGroup(name=name, description=description, office_id=office_id)
    db.session.add(g)
    db.session.commit()
    return jsonify({'ok': True, 'group': g.to_dict()})


@bp.delete('/api/groups/<int:group_id>')
def api_groups_delete(group_id: int):
    office_id = get_selected_office_id()
    g = DeviceGroup.query.get(group_id)
    if not g or g.office_id != office_id:
        return _json_error('分组不存在', 404)

    db.session.delete(g)
    db.session.commit()
    return jsonify({'ok': True})


@bp.post('/api/groups/<int:group_id>/members')
def api_groups_add_member(group_id: int):
    office_id = get_selected_office_id()
    g = DeviceGroup.query.get(group_id)
    if not g or g.office_id != office_id:
        return _json_error('分组不存在', 404)

    payload = request.get_json(silent=True) or {}
    device_id = payload.get('device_id')
    try:
        device_id = int(device_id)
    except Exception:
        return _json_error('device_id 无效')

    d = Device.query.get(device_id)
    if not d or d.office_id != office_id:
        return _json_error('设备不存在', 404)

    exists = DeviceGroupMember.query.filter_by(group_id=group_id, device_id=device_id).first()
    if exists:
        return jsonify({'ok': True})

    m = DeviceGroupMember(group_id=group_id, device_id=device_id)
    db.session.add(m)
    db.session.commit()
    return jsonify({'ok': True})


@bp.delete('/api/groups/<int:group_id>/members/<int:device_id>')
def api_groups_remove_member(group_id: int, device_id: int):
    office_id = get_selected_office_id()
    g = DeviceGroup.query.get(group_id)
    if not g or g.office_id != office_id:
        return _json_error('分组不存在', 404)
    d = Device.query.get(device_id)
    if not d or d.office_id != office_id:
        return _json_error('设备不存在', 404)
    m = DeviceGroupMember.query.filter_by(group_id=group_id, device_id=device_id).first()
    if not m:
        return _json_error('成员关系不存在', 404)

    db.session.delete(m)
    db.session.commit()
    return jsonify({'ok': True})


@bp.post('/api/groups/<int:group_id>/set_state')
def api_groups_set_state(group_id: int):
    office_id = get_selected_office_id()
    g = DeviceGroup.query.get(group_id)
    if not g or g.office_id != office_id:
        return _json_error('分组不存在', 404)

    payload = request.get_json(silent=True) or {}
    is_on = payload.get('is_on')
    if not isinstance(is_on, bool):
        return _json_error('is_on 必须是布尔值 true/false')

    device_ids = [
        r[0]
        for r in db.session.query(DeviceGroupMember.device_id)
        .filter(DeviceGroupMember.group_id == group_id)
        .all()
    ]
    if device_ids:
        # 先取出当前状态，逐台记录变更日志（只在状态变化时记录）
        devices = Device.query.filter(Device.id.in_(device_ids), Device.office_id == office_id).all()
        changed = 0
        for d in devices:
            if log_device_state_change(d, is_on, source='group'):
                changed += 1
                d.is_on = is_on
        db.session.commit()
    return jsonify({'ok': True, 'affected': len(device_ids)})


@bp.get('/api/devices')
def api_devices_list():
    office_id = get_selected_office_id()
    devices = Device.query.filter_by(office_id=office_id).order_by(Device.id.desc()).all()
    return jsonify({'ok': True, 'devices': [d.to_dict(with_groups=True) for d in devices]})


@bp.post('/api/devices')
def api_devices_create():
    office_id = get_selected_office_id()
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    device_type = (payload.get('device_type') or '').strip()
    location = (payload.get('location') or '').strip()

    if not name:
        return _json_error('设备名称不能为空')
    if not device_type:
        return _json_error('设备类型不能为空')

    d = Device(name=name, device_type=device_type, location=location, is_on=False, office_id=office_id)
    db.session.add(d)
    db.session.commit()
    return jsonify({'ok': True, 'device': d.to_dict(with_groups=True)})


@bp.delete('/api/devices/<int:device_id>')
def api_devices_delete(device_id: int):
    office_id = get_selected_office_id()
    d = Device.query.get(device_id)
    if not d or d.office_id != office_id:
        return _json_error('设备不存在', 404)

    db.session.delete(d)
    db.session.commit()
    return jsonify({'ok': True})


@bp.post('/api/devices/<int:device_id>/set_state')
def api_devices_set_state(device_id: int):
    office_id = get_selected_office_id()
    d = Device.query.get(device_id)
    if not d or d.office_id != office_id:
        return _json_error('设备不存在', 404)

    payload = request.get_json(silent=True) or {}
    is_on = payload.get('is_on')
    if not isinstance(is_on, bool):
        return _json_error('is_on 必须是布尔值 true/false')

    log_device_state_change(d, is_on, source='manual')
    d.is_on = is_on
    db.session.commit()
    return jsonify({'ok': True, 'device': d.to_dict(with_groups=True)})
