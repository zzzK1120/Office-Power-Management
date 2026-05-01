"""首页蓝图：办公室选择 + 模块入口"""
from flask import Blueprint, jsonify, render_template, request, session

from extensions import db
from models import Device, DeviceGroup, Office, Schedule

bp = Blueprint('home', __name__, url_prefix='/')


@bp.route('/')
def index():
    """首页：办公室选择与模块入口"""
    return render_template('home.html')


def _json_error(message: str, status_code: int = 400):
    return jsonify({'ok': False, 'error': message}), status_code


@bp.get('/api/offices')
def api_offices():
    offices = Office.query.order_by(Office.id.asc()).all()
    selected_id = session.get('office_id')
    if selected_id is None and offices:
        selected_id = offices[0].id
        session['office_id'] = int(selected_id)
    office_ids = [o.id for o in offices]
    stats_map = {}
    if office_ids:
        # (office_id, total_cnt, on_cnt)
        rows = (
            db.session.query(
                Device.office_id,
                db.func.count(Device.id),
                db.func.sum(db.case((Device.is_on.is_(True), 1), else_=0)),
            )
            .filter(Device.office_id.in_(office_ids))
            .group_by(Device.office_id)
            .all()
        )
        for oid, total_cnt, on_cnt in rows:
            stats_map[int(oid)] = {
                'total_devices': int(total_cnt or 0),
                'on_devices': int(on_cnt or 0),
            }
    return jsonify(
        {
            'ok': True,
            'selected_office_id': int(selected_id) if selected_id else None,
            'offices': [
                dict(o.to_dict(), **stats_map.get(int(o.id), {'total_devices': 0, 'on_devices': 0}))
                for o in offices
            ],
        }
    )


@bp.post('/api/offices')
def api_create_office():
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    location = (payload.get('location') or '').strip()
    description = (payload.get('description') or '').strip()
    if not name:
        return _json_error('办公室名称不能为空')
    if Office.query.filter_by(name=name).first():
        return _json_error('办公室名称已存在')
    row = Office(name=name, location=location, description=description)
    db.session.add(row)
    db.session.commit()
    return jsonify({'ok': True, 'office': row.to_dict()})


@bp.put('/api/offices/<int:office_id>')
def api_update_office(office_id: int):
    row = Office.query.get(office_id)
    if not row:
        return _json_error('办公室不存在', 404)
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or row.name).strip()
    location = (payload.get('location') or '').strip()
    description = (payload.get('description') or '').strip()
    if not name:
        return _json_error('办公室名称不能为空')
    other = Office.query.filter(Office.name == name, Office.id != office_id).first()
    if other:
        return _json_error('办公室名称已存在')
    row.name = name
    row.location = location
    row.description = description
    db.session.commit()
    return jsonify({'ok': True, 'office': row.to_dict()})


@bp.delete('/api/offices/<int:office_id>')
def api_delete_office(office_id: int):
    row = Office.query.get(office_id)
    if not row:
        return _json_error('办公室不存在', 404)
    if Office.query.count() <= 1:
        return _json_error('至少保留一个办公室')
    if Device.query.filter_by(office_id=office_id).first():
        return _json_error('该办公室下存在设备，请先迁移或删除设备')
    if DeviceGroup.query.filter_by(office_id=office_id).first():
        return _json_error('该办公室下存在分组，请先删除分组')
    if Schedule.query.filter_by(office_id=office_id).first():
        return _json_error('该办公室下存在定时策略，请先删除策略')
    db.session.delete(row)
    db.session.commit()
    if session.get('office_id') == office_id:
        first = Office.query.order_by(Office.id.asc()).first()
        session['office_id'] = first.id if first else None
    return jsonify({'ok': True})


@bp.post('/api/select-office')
def api_select_office():
    payload = request.get_json(silent=True) or {}
    office_id = payload.get('office_id')
    try:
        office_id = int(office_id)
    except Exception:
        return _json_error('office_id 无效')
    row = Office.query.get(office_id)
    if not row:
        return _json_error('办公室不存在', 404)
    session['office_id'] = row.id
    return jsonify({'ok': True, 'office': row.to_dict()})
