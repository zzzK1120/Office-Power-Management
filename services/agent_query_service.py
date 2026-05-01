"""AI 助手查询能力服务。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, func, or_

from extensions import db
from models import Device, EnergyDaily, Office


def normalize_date_range(start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date]:
    """归一化日期范围。"""
    today = date.today()
    safe_days = max(int(default_days or 30), 1)
    normalized_end = end or today
    normalized_start = start or (normalized_end - timedelta(days=safe_days - 1))
    if normalized_start > normalized_end:
        normalized_start, normalized_end = normalized_end, normalized_start
    return normalized_start, normalized_end


def get_office_display_name(office: Office | None) -> str | None:
    if not office:
        return None
    loc = (office.location or '').strip()
    name = (office.name or '').strip()
    return f'{loc} · {name}' if (loc and name) else (name or loc or None)


def resolve_office_by_name(office_name: str | None) -> Office | None:
    if not office_name:
        return None
    keyword = office_name.strip()
    if not keyword:
        return None

    offices = Office.query.order_by(Office.id.asc()).all()
    for office in offices:
        variants = {
            (office.name or '').strip(),
            (office.location or '').strip(),
            ((office.location or '').strip() + (office.name or '').strip()).strip(),
            (get_office_display_name(office) or '').replace(' · ', ''),
            (get_office_display_name(office) or '').strip(),
        }
        variants = {item for item in variants if item}
        if keyword in variants:
            return office
        if any(keyword in item for item in variants):
            return office
        if any(item in keyword for item in variants):
            return office

    return (
        Office.query.filter(
            or_(
                Office.name.contains(keyword),
                Office.location.contains(keyword),
                Office.description.contains(keyword),
            )
        )
        .order_by(Office.id.asc())
        .first()
    )


def _apply_device_type_filter(query, device_type: str | None):
    if not device_type:
        return query
    keyword = device_type.strip()
    if not keyword:
        return query

    aliases = {keyword}
    if keyword in {'灯', '照明', '灯光'}:
        aliases.update({'灯', '照明', '灯光'})

    conditions = []
    for alias in aliases:
        conditions.extend(
            [
                Device.device_type.contains(alias),
                Device.name.contains(alias),
                Device.location.contains(alias),
            ]
        )
    return query.filter(or_(*conditions))


def find_unclosed_devices(office_name: str | None = None, device_type: str | None = None) -> dict:
    office = resolve_office_by_name(office_name)
    query = (
        db.session.query(Device, Office)
        .outerjoin(Office, Office.id == Device.office_id)
        .filter(Device.is_on.is_(True))
    )

    if office:
        query = query.filter(Device.office_id == office.id)
    elif office_name:
        query = query.filter(or_(Office.name.contains(office_name), Office.location.contains(office_name)))

    query = _apply_device_type_filter(query, device_type)
    rows = query.order_by(Office.id.asc(), Device.id.asc()).all()

    offices_map: dict[int, dict] = {}
    devices = []
    for device, office_row in rows:
        office_id = int(office_row.id) if office_row else 0
        office_display = get_office_display_name(office_row) or '未分配办公室'
        if office_id not in offices_map:
            offices_map[office_id] = {
                'name': office_row.name if office_row else '未分配办公室',
                'location': office_row.location if office_row else '',
                'display_name': office_display,
                'open_count': 0,
            }
        offices_map[office_id]['open_count'] += 1
        devices.append(
            {
                'id': int(device.id),
                'name': device.name,
                'office_id': int(device.office_id) if device.office_id is not None else None,
                'office_name': office_display,
                'device_type': device.device_type or '其他',
                'location': device.location or '',
                'is_on': bool(device.is_on),
            }
        )

    offices = sorted(offices_map.values(), key=lambda item: item['open_count'], reverse=True)
    return {
        'offices': offices,
        'devices': devices,
        'summary': {
            'office_count': len(offices),
            'device_count': len(devices),
        },
        'office_name': get_office_display_name(office) if office else None,
        'device_type': device_type,
    }


def compare_offices_energy(start: date, end: date) -> dict:
    rows = (
        db.session.query(
            Office.id,
            Office.name,
            Office.location,
            func.count(func.distinct(Device.id)).label('device_count'),
            func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('energy_kwh'),
            func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('cost_estimated'),
            func.max(EnergyDaily.peak_power_w).label('peak_power_w'),
        )
        .outerjoin(Device, Device.office_id == Office.id)
        .outerjoin(
            EnergyDaily,
            and_(
                EnergyDaily.device_id == Device.id,
                EnergyDaily.date >= start,
                EnergyDaily.date <= end,
            ),
        )
        .group_by(Office.id, Office.name, Office.location)
        .order_by(func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).desc(), Office.id.asc())
        .all()
    )

    items = []
    total_kwh = 0.0
    for row in rows:
        energy_kwh = round(float(row.energy_kwh or 0.0), 3)
        total_kwh += energy_kwh
        items.append(
            {
                'office_id': int(row.id),
                'office_name': row.name,
                'location': row.location or '',
                'display_name': f"{row.location} · {row.name}" if (row.location and row.name) else (row.name or row.location or ''),
                'device_count': int(row.device_count or 0),
                'energy_kwh': energy_kwh,
                'cost_estimated': round(float(row.cost_estimated or 0.0), 2),
                'peak_power_kw': round(float(row.peak_power_w or 0.0) / 1000.0, 3),
            }
        )

    total_kwh = round(total_kwh, 3)
    for item in items:
        item['share_pct'] = round(100.0 * item['energy_kwh'] / total_kwh, 1) if total_kwh > 0 else 0.0

    return {
        'start': start.isoformat(),
        'end': end.isoformat(),
        'rows': items,
        'summary': {
            'total_kwh': total_kwh,
            'office_count': len(items),
        },
        'top_office': items[0] if items else None,
        'top3': items[:3],
    }


def summarize_top_office_in_range(start: date, end: date) -> dict:
    return compare_offices_energy(start, end)


def get_device_energy_report(start: date, end: date, office_id: int | None = None) -> dict:
    query = (
        db.session.query(
            Device.id,
            Device.name,
            Device.device_type,
            Device.location,
            Device.office_id,
            Office.name.label('office_name'),
            Office.location.label('office_location'),
            func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('total_kwh'),
            func.max(EnergyDaily.peak_power_w).label('peak_w'),
            func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('total_cost'),
        )
        .outerjoin(Office, Office.id == Device.office_id)
        .outerjoin(
            EnergyDaily,
            and_(
                EnergyDaily.device_id == Device.id,
                EnergyDaily.date >= start,
                EnergyDaily.date <= end,
            ),
        )
    )
    if office_id:
        query = query.filter(Device.office_id == office_id)

    rows = query.group_by(
        Device.id,
        Device.name,
        Device.device_type,
        Device.location,
        Device.office_id,
        Office.name,
        Office.location,
    ).all()

    devices = []
    for row in rows:
        energy_kwh = round(float(row.total_kwh or 0.0), 3)
        office_display = f"{row.office_location} · {row.office_name}" if (row.office_location and row.office_name) else (row.office_name or row.office_location or '')
        devices.append(
            {
                'device_id': int(row.id),
                'name': row.name,
                'device_type': row.device_type or '其他',
                'location': row.location or '',
                'office_id': int(row.office_id) if row.office_id is not None else None,
                'office_name': office_display,
                'energy_kwh': energy_kwh,
                'peak_power_kw': round(float(row.peak_w or 0.0) / 1000.0, 3),
                'cost_estimated': round(float(row.total_cost or 0.0), 2),
            }
        )

    devices.sort(key=lambda item: item['energy_kwh'], reverse=True)
    total_kwh = round(sum(item['energy_kwh'] for item in devices), 3)
    total_cost = round(sum(item['cost_estimated'] for item in devices), 2)
    for item in devices:
        item['share_pct'] = round(100.0 * item['energy_kwh'] / total_kwh, 1) if total_kwh > 0 else 0.0

    office = Office.query.get(office_id) if office_id else None
    return {
        'office_id': office_id,
        'office_name': get_office_display_name(office) if office else None,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'devices': devices,
        'summary': {
            'total_kwh': total_kwh,
            'total_cost': total_cost,
            'device_count': len(devices),
        },
        'top_device': devices[0] if devices else None,
    }


def get_office_energy_report(office_id: int, start: date, end: date) -> dict:
    office = Office.query.get(office_id)
    if not office:
        raise ValueError('办公室不存在')

    summary_row = (
        db.session.query(
            func.count(func.distinct(Device.id)).label('device_count'),
            func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('energy_kwh'),
            func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('cost_estimated'),
            func.max(EnergyDaily.peak_power_w).label('peak_power_w'),
        )
        .outerjoin(
            EnergyDaily,
            and_(
                EnergyDaily.device_id == Device.id,
                EnergyDaily.date >= start,
                EnergyDaily.date <= end,
            ),
        )
        .filter(Device.office_id == office_id)
        .one()
    )

    device_report = get_device_energy_report(start, end, office_id=office_id)
    return {
        'office_id': int(office.id),
        'office_name': get_office_display_name(office) or office.name,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'summary': {
            'device_count': int(summary_row.device_count or 0),
            'total_kwh': round(float(summary_row.energy_kwh or 0.0), 3),
            'total_cost': round(float(summary_row.cost_estimated or 0.0), 2),
            'peak_power_kw': round(float(summary_row.peak_power_w or 0.0) / 1000.0, 3),
        },
        'top_devices': device_report['devices'][:5],
    }
