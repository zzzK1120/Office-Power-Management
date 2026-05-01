"""异常报警：阈值规则 + 报警记录"""
from extensions import db


class AlarmRule(db.Model):
    __tablename__ = 'alarm_rules'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # 作用范围：DEVICE / TYPE / ALL
    scope_type = db.Column(db.String(16), nullable=False, default='DEVICE')
    # scope_key: DEVICE 时存 device_id；TYPE 时存类型标识（如 'AC'）；ALL 时为空字符串
    scope_key = db.Column(db.String(64), nullable=False, default='')

    voltage_min = db.Column(db.Float, nullable=True, comment='电压下限(V)')
    voltage_max = db.Column(db.Float, nullable=True, comment='电压上限(V)')
    power_max = db.Column(db.Float, nullable=True, comment='功率上限(W)')
    current_max = db.Column(db.Float, nullable=True, comment='电流上限(A)')
    pf_min = db.Column(db.Float, nullable=True, comment='功率因数下限')

    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.Index('ix_alarm_rules_scope', 'scope_type', 'scope_key'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'scope_type': self.scope_type,
            'scope_key': self.scope_key,
            'voltage_min': self.voltage_min,
            'voltage_max': self.voltage_max,
            'power_max': self.power_max,
            'current_max': self.current_max,
            'pf_min': self.pf_min,
            'enabled': self.enabled,
        }


class Alarm(db.Model):
    __tablename__ = 'alarms'

    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='SET NULL'), nullable=True)

    alarm_type = db.Column(db.String(32), nullable=False, comment='报警类型')
    message = db.Column(db.String(255), nullable=False, comment='报警描述')
    ts = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), comment='报警时间')

    value = db.Column(db.Float, nullable=True, comment='触发时数值')
    threshold = db.Column(db.Float, nullable=True, comment='阈值')

    # 状态：NEW / ACK / RESOLVED
    status = db.Column(db.String(16), nullable=False, default='NEW')
    handled_at = db.Column(db.DateTime, nullable=True)
    handled_by = db.Column(db.String(64), nullable=True, comment='处理人')

    __table_args__ = (
        db.Index('ix_alarms_status_ts', 'status', 'ts'),
        db.Index('ix_alarms_device_ts', 'device_id', 'ts'),
    )

    device = db.relationship('Device', lazy='joined')

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'alarm_type': self.alarm_type,
            'message': self.message,
            'ts': self.ts.isoformat() if self.ts else None,
            'value': self.value,
            'threshold': self.threshold,
            'status': self.status,
            'handled_at': self.handled_at.isoformat() if self.handled_at else None,
            'handled_by': self.handled_by,
        }

