"""办公室上下文：当前选中办公室解析与兜底。"""
from __future__ import annotations

from flask import session

from extensions import db
from models import Office


def _ensure_default_office() -> Office:
    row = Office.query.order_by(Office.id.asc()).first()
    if row:
        return row
    row = Office(name='默认办公室', location='', description='系统自动创建')
    db.session.add(row)
    db.session.commit()
    return row


def get_selected_office_id() -> int:
    """返回当前会话选中的办公室ID；若未选中则回退到第一个办公室并写回 session。"""
    raw = session.get('office_id')
    if raw is not None:
        try:
            oid = int(raw)
            if Office.query.get(oid):
                return oid
        except Exception:
            pass
    row = _ensure_default_office()
    session['office_id'] = row.id
    return int(row.id)
