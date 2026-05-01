"""办公室模型"""
from extensions import db


class Office(db.Model):
    __tablename__ = 'offices'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(64), nullable=False, unique=True, comment='办公室名称')
    location = db.Column(db.String(128), default='', comment='办公室位置/楼层')
    description = db.Column(db.String(255), default='', comment='备注')
    smart_close_enabled = db.Column(
        db.Boolean, nullable=False, default=False, comment='智能关闭：作息学习+区域联动'
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        loc = (self.location or '').strip()
        name = (self.name or '').strip()
        display = f'{loc} · {name}' if (loc and name) else (name or loc or '')
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location or '',
            'description': self.description or '',
            'smart_close_enabled': bool(self.smart_close_enabled),
            'display_name': display,
        }
