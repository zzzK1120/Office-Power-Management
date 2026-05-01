"""设备采样/遥测数据（实时监控、报警、统计的基础数据）"""
from extensions import db


class DeviceTelemetry(db.Model):
    __tablename__ = 'device_telemetry'

    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False)
    ts = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), comment='采样时间')

    voltage = db.Column(db.Float, nullable=True, comment='电压(V)')
    current = db.Column(db.Float, nullable=True, comment='电流(A)')
    power = db.Column(db.Float, nullable=True, comment='功率(W)')
    pf = db.Column(db.Float, nullable=True, comment='功率因数')
    energy_kwh_total = db.Column(db.Float, nullable=True, comment='累计电量(kWh)')

    __table_args__ = (
        db.Index('ix_telemetry_device_ts', 'device_id', 'ts'),
    )

    device = db.relationship('Device', lazy='joined')

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'ts': self.ts.isoformat() if self.ts else None,
            'voltage': self.voltage,
            'current': self.current,
            'power': self.power,
            'pf': self.pf,
            'energy_kwh_total': self.energy_kwh_total,
        }

