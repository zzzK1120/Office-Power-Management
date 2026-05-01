"""用电统计蓝图：日/周/月报表"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import and_, func

from extensions import db
from models import Device, EnergyDaily, Office

bp = Blueprint('statistics', __name__, url_prefix='/statistics')


def _parse_office_id(raw):
    if raw in (None, '', 'ALL', 'all'):
        return None
    try:
        return int(raw)
    except Exception:
        return None


@bp.route('/')
def index():
    """基础用电统计页"""
    return render_template('statistics.html')


@bp.get('/api/daily')
def api_daily():
    """近30天按日汇总（默认全局；传 office_id 则按办公室聚合）"""
    office_id = _parse_office_id(request.args.get('office_id'))
    start = date.today() - timedelta(days=29)
    if office_id:
        # 按办公室：从 device_id 维度的日汇总聚合
        agg = (
            db.session.query(
                EnergyDaily.date.label('date'),
                func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('energy_kwh'),
                func.max(EnergyDaily.peak_power_w).label('peak_power_w'),
                func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('cost_estimated'),
            )
            .join(Device, Device.id == EnergyDaily.device_id)
            .filter(EnergyDaily.date >= start, Device.office_id == office_id)
            .group_by(EnergyDaily.date)
            .order_by(EnergyDaily.date.asc())
            .all()
        )
        data = [
            {
                'date': r.date.isoformat() if r.date else None,
                'device_id': None,
                'energy_kwh': float(r.energy_kwh or 0.0),
                'peak_power_w': float(r.peak_power_w or 0.0),
                'cost_estimated': float(r.cost_estimated or 0.0),
            }
            for r in agg
        ]
        total = round(sum(float(r['energy_kwh'] or 0.0) for r in data), 3)
        avg = round((total / len(data)) if data else 0.0, 3)
        office = Office.query.get(office_id)
        return jsonify(
            {
                'ok': True,
                'office_id': office_id,
                'office_name': office.name if office else None,
                'rows': data,
                'summary': {'month_total_kwh': total, 'daily_avg_kwh': avg},
            }
        )
    else:
        rows = (
            EnergyDaily.query.filter(EnergyDaily.date >= start, EnergyDaily.device_id.is_(None))
            .order_by(EnergyDaily.date.asc())
            .all()
        )
        data = [r.to_dict() for r in rows]
        total = round(sum(float(r.energy_kwh or 0.0) for r in rows), 3)
        avg = round((total / len(rows)) if rows else 0.0, 3)
        return jsonify({'ok': True, 'rows': data, 'summary': {'month_total_kwh': total, 'daily_avg_kwh': avg}})


def _week_label(iso_year: int, iso_week: int) -> str:
    mon = date.fromisocalendar(iso_year, iso_week, 1)
    sun = date.fromisocalendar(iso_year, iso_week, 7)
    return f'{iso_year}年第{iso_week}周 ({mon.month}/{mon.day}-{sun.month}/{sun.day})'


@bp.get('/api/weekly')
def api_weekly():
    """近 12 个自然周（ISO 周）用电量（默认全局；传 office_id 则按办公室聚合）"""
    office_id = _parse_office_id(request.args.get('office_id'))
    today = date.today()
    # 多取几天保证覆盖到完整 ISO 周
    start = today - timedelta(days=12 * 7 + 7)
    if office_id:
        rows = (
            db.session.query(
                EnergyDaily.date.label('date'),
                func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('energy_kwh'),
                func.max(EnergyDaily.peak_power_w).label('peak_power_w'),
                func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('cost_estimated'),
            )
            .join(Device, Device.id == EnergyDaily.device_id)
            .filter(EnergyDaily.date >= start, EnergyDaily.date <= today, Device.office_id == office_id)
            .group_by(EnergyDaily.date)
            .order_by(EnergyDaily.date.asc())
            .all()
        )
    else:
        rows = (
            EnergyDaily.query.filter(
                EnergyDaily.date >= start,
                EnergyDaily.date <= today,
                EnergyDaily.device_id.is_(None),
            )
            .order_by(EnergyDaily.date.asc())
            .all()
        )

    bucket: dict[tuple[int, int], dict] = defaultdict(
        lambda: {'energy_kwh': 0.0, 'cost_estimated': 0.0, 'peak_power_w': 0.0, 'day_count': 0}
    )
    for r in rows:
        dt = getattr(r, 'date', None)
        if not dt:
            continue
        y, w, _ = dt.isocalendar()
        b = bucket[(y, w)]
        b['energy_kwh'] += float(getattr(r, 'energy_kwh', None) or getattr(r, 'energy_kwh', 0.0) or 0.0)
        b['cost_estimated'] += float(getattr(r, 'cost_estimated', None) or 0.0)
        pw = float(getattr(r, 'peak_power_w', None) or 0.0)
        b['peak_power_w'] = max(b['peak_power_w'], pw)
        b['day_count'] += 1

    # 按周排序，取最近 12 周（新→旧）
    keys_sorted = sorted(bucket.keys(), reverse=True)[:12]
    out = []
    for y, w in keys_sorted:
        b = bucket[(y, w)]
        ek = b['energy_kwh']
        out.append(
            {
                'label': _week_label(y, w),
                'iso_year': y,
                'iso_week': w,
                'energy_kwh': round(ek, 3),
                'daily_avg_kwh': round(ek / 7.0, 3),
                'cost_estimated': round(b['cost_estimated'], 2),
                'peak_power_kw': round(b['peak_power_w'] / 1000.0, 3),
                'day_count': b['day_count'],
            }
        )
    office = Office.query.get(office_id) if office_id else None
    return jsonify({'ok': True, 'office_id': office_id, 'office_name': office.name if office else None, 'rows': out})


def _days_for_month_avg(year: int, month: int, today: date) -> int:
    """当月已过天数（含今天）；历史整月用该月总天数"""
    if year == today.year and month == today.month:
        return today.day
    return calendar.monthrange(year, month)[1]


@bp.get('/api/monthly')
def api_monthly():
    """近 12 个自然月用电量（默认全局；传 office_id 则按办公室聚合）"""
    office_id = _parse_office_id(request.args.get('office_id'))
    today = date.today()
    start = date(today.year, today.month, 1) - timedelta(days=365 + 62)
    if office_id:
        rows = (
            db.session.query(
                EnergyDaily.date.label('date'),
                func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('energy_kwh'),
                func.max(EnergyDaily.peak_power_w).label('peak_power_w'),
                func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('cost_estimated'),
            )
            .join(Device, Device.id == EnergyDaily.device_id)
            .filter(EnergyDaily.date >= start, EnergyDaily.date <= today, Device.office_id == office_id)
            .group_by(EnergyDaily.date)
            .order_by(EnergyDaily.date.asc())
            .all()
        )
    else:
        rows = (
            EnergyDaily.query.filter(
                EnergyDaily.date >= start,
                EnergyDaily.date <= today,
                EnergyDaily.device_id.is_(None),
            )
            .order_by(EnergyDaily.date.asc())
            .all()
        )

    bucket: dict[tuple[int, int], dict] = defaultdict(
        lambda: {'energy_kwh': 0.0, 'cost_estimated': 0.0, 'peak_power_w': 0.0, 'day_count': 0}
    )
    for r in rows:
        dt = getattr(r, 'date', None)
        if not dt:
            continue
        key = (dt.year, dt.month)
        b = bucket[key]
        b['energy_kwh'] += float(getattr(r, 'energy_kwh', None) or 0.0)
        b['cost_estimated'] += float(getattr(r, 'cost_estimated', None) or 0.0)
        pw = float(getattr(r, 'peak_power_w', None) or 0.0)
        b['peak_power_w'] = max(b['peak_power_w'], pw)
        b['day_count'] += 1

    keys_sorted = sorted(bucket.keys(), reverse=True)[:12]
    out = []
    for y, m in keys_sorted:
        b = bucket[(y, m)]
        ek = b['energy_kwh']
        div = _days_for_month_avg(y, m, today)
        daily_avg = round(ek / div, 3) if div else 0.0
        out.append(
            {
                'label': f'{y}-{m:02d}',
                'year': y,
                'month': m,
                'energy_kwh': round(ek, 3),
                'daily_avg_kwh': daily_avg,
                'cost_estimated': round(b['cost_estimated'], 2),
                'peak_power_kw': round(b['peak_power_w'] / 1000.0, 3),
                'day_count': b['day_count'],
            }
        )
    office = Office.query.get(office_id) if office_id else None
    return jsonify({'ok': True, 'office_id': office_id, 'office_name': office.name if office else None, 'rows': out})


@bp.get('/api/compare-offices')
def api_compare_offices():
    """办公室横向对比：按日期范围汇总每个办公室总用电/电费/峰值功率/设备数"""
    today = date.today()
    end = _parse_date(request.args.get('end'), today)
    start = _parse_date(request.args.get('start'), today - timedelta(days=29))
    if start > end:
        start, end = end, start

    rows = (
        db.session.query(
            Office.id,
            Office.name,
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
        .group_by(Office.id, Office.name)
        .order_by(func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).desc())
        .all()
    )
    out = []
    total = 0.0
    for r in rows:
        ek = float(r.energy_kwh or 0.0)
        total += ek
        out.append(
            {
                'office_id': int(r.id),
                'office_name': r.name,
                'device_count': int(r.device_count or 0),
                'energy_kwh': round(ek, 3),
                'cost_estimated': round(float(r.cost_estimated or 0.0), 2),
                'peak_power_kw': round(float(r.peak_power_w or 0.0) / 1000.0, 3),
            }
        )
    total = round(total, 3)
    for x in out:
        x['share_pct'] = round(100.0 * x['energy_kwh'] / total, 1) if total > 0 else 0.0
    return jsonify({'ok': True, 'start': start.isoformat(), 'end': end.isoformat(), 'rows': out, 'summary': {'total_kwh': total}})


def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return default


@bp.get('/api/by-device')
def api_by_device():
    """指定日期范围内各设备用电量汇总（用于用电报表表格；可传 office_id 过滤）"""
    office_id = _parse_office_id(request.args.get('office_id'))
    today = date.today()
    end = _parse_date(request.args.get('end'), today)
    start = _parse_date(request.args.get('start'), today - timedelta(days=29))
    if start > end:
        start, end = end, start

    q = (
        db.session.query(
            Device.id,
            Device.name,
            Device.device_type,
            Device.location,
            func.coalesce(func.sum(EnergyDaily.energy_kwh), 0.0).label('total_kwh'),
            func.max(EnergyDaily.peak_power_w).label('peak_w'),
            func.coalesce(func.sum(EnergyDaily.cost_estimated), 0.0).label('total_cost'),
        )
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
        q = q.filter(Device.office_id == office_id)
    q = q.group_by(Device.id, Device.name, Device.device_type, Device.location)
    raw_rows = q.all()
    items = []
    for r in raw_rows:
        ek = float(r.total_kwh or 0.0)
        pw = float(r.peak_w or 0.0) if r.peak_w is not None else 0.0
        items.append(
            {
                'device_id': r.id,
                'name': r.name,
                'device_type': r.device_type or '其他',
                'location': r.location or '',
                'energy_kwh': round(ek, 3),
                'peak_power_kw': round(pw / 1000.0, 3),
                'cost_estimated': round(float(r.total_cost or 0.0), 2),
            }
        )
    items.sort(key=lambda x: x['energy_kwh'], reverse=True)
    total_kwh = round(sum(x['energy_kwh'] for x in items), 3)
    total_cost = round(sum(x['cost_estimated'] for x in items), 2)
    for x in items:
        x['share_pct'] = round(100.0 * x['energy_kwh'] / total_kwh, 1) if total_kwh > 0 else 0.0

    office = Office.query.get(office_id) if office_id else None
    return jsonify(
        {
            'ok': True,
            'office_id': office_id,
            'office_name': office.name if office else None,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'devices': items,
            'summary': {
                'total_kwh': total_kwh,
                'total_cost': total_cost,
                'device_count': len(items),
            },
        }
    )


@bp.get('/api/daily-per-device')
def api_daily_per_device():
    """按日、按设备分解用电量，用于堆叠面积/多系列曲线（可传 office_id 过滤；设备过多时合并为「其他」）"""
    office_id = _parse_office_id(request.args.get('office_id'))
    today = date.today()
    end = _parse_date(request.args.get('end'), today)
    start = _parse_date(request.args.get('start'), today - timedelta(days=29))
    if start > end:
        start, end = end, start

    dq = Device.query
    if office_id:
        dq = dq.filter_by(office_id=office_id)
    devices = dq.order_by(Device.id.asc()).all()
    date_list: list[date] = []
    d = start
    while d <= end:
        date_list.append(d)
        d += timedelta(days=1)

    rows = EnergyDaily.query.filter(
        EnergyDaily.device_id.isnot(None),
        EnergyDaily.date >= start,
        EnergyDaily.date <= end,
    ).all()
    cell: dict[tuple[int, date], float] = {}
    for r in rows:
        if r.device_id is None or r.date is None:
            continue
        cell[(r.device_id, r.date)] = float(r.energy_kwh or 0.0)

    raw_series: list[dict] = []
    for dev in devices:
        vals = [round(cell.get((dev.id, dt), 0.0), 4) for dt in date_list]
        raw_series.append(
            {
                'name': dev.name or f'设备{dev.id}',
                'data': vals,
                'total': sum(vals),
            }
        )
    raw_series.sort(key=lambda x: x['total'], reverse=True)

    max_series = 12
    if len(raw_series) > max_series:
        top = raw_series[:max_series]
        rest = raw_series[max_series:]
        other_data = [0.0] * len(date_list)
        for s in rest:
            for i, v in enumerate(s['data']):
                other_data[i] += v
        other_data = [round(x, 4) for x in other_data]
        top.append(
            {
                'name': f'其他（{len(rest)} 台）',
                'data': other_data,
            }
        )
        series_out = [{'name': s['name'], 'data': s['data']} for s in top]
    else:
        series_out = [{'name': s['name'], 'data': s['data']} for s in raw_series]

    office = Office.query.get(office_id) if office_id else None
    return jsonify(
        {
            'ok': True,
            'office_id': office_id,
            'office_name': office.name if office else None,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'dates': [x.isoformat() for x in date_list],
            'series': series_out,
        }
    )
