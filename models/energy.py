"""用电统计汇总（建议按天落库，周/月可由天聚合得到）"""
from extensions import db


class EnergyDaily(db.Model):
    __tablename__ = 'energy_daily'

    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    date = db.Column(db.Date, nullable=False, comment='统计日期')

    # device_id 允许为空，表示全办公室/全系统的总汇总
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='SET NULL'), nullable=True)

    energy_kwh = db.Column(db.Float, nullable=False, default=0.0, comment='用电量(kWh)')
    peak_power_w = db.Column(db.Float, nullable=True, comment='峰值功率(W)')
    cost_estimated = db.Column(db.Float, nullable=True, comment='电费估算(元)')
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('date', 'device_id', name='uq_energy_daily_date_device'),
        db.Index('ix_energy_daily_date', 'date'),
    )

    device = db.relationship('Device', lazy='joined')

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'device_id': self.device_id,
            'energy_kwh': self.energy_kwh,
            'peak_power_w': self.peak_power_w,
            'cost_estimated': self.cost_estimated,
        }

