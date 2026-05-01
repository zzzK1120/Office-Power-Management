"""设备开关状态变更日志（用于监控页查询开/关记录）"""
from extensions import db


class DeviceStateLog(db.Model):
    __tablename__ = 'device_state_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False)
    office_id = db.Column(db.Integer, db.ForeignKey('offices.id', ondelete='SET NULL'), nullable=True)

    ts = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), comment='变更时间')
    is_on = db.Column(db.Boolean, nullable=False, comment='变更后状态')
    source = db.Column(db.String(32), nullable=False, default='manual', comment='manual/schedule/group')

    __table_args__ = (
        db.Index('ix_state_logs_device_ts', 'device_id', 'ts'),
        db.Index('ix_state_logs_office_ts', 'office_id', 'ts'),
    )

    device = db.relationship('Device', lazy='joined')

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'office_id': self.office_id,
            'ts': self.ts.isoformat() if self.ts else None,
            'is_on': bool(self.is_on),
            'source': self.source,
        }
