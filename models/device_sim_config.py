"""设备级模拟参数配置"""
from extensions import db


class DeviceSimConfig(db.Model):
    __tablename__ = 'device_sim_configs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False, unique=True)

    voltage_min = db.Column(db.Float, nullable=True)
    voltage_max = db.Column(db.Float, nullable=True)
    power_min = db.Column(db.Float, nullable=True)
    power_max = db.Column(db.Float, nullable=True)
    anomaly_prob_voltage = db.Column(db.Float, nullable=True)
    anomaly_prob_power = db.Column(db.Float, nullable=True)

    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    device = db.relationship('Device', lazy='joined')

    def to_dict(self):
        return {
            'device_id': self.device_id,
            'voltage_min': self.voltage_min,
            'voltage_max': self.voltage_max,
            'power_min': self.power_min,
            'power_max': self.power_max,
            'anomaly_prob_voltage': self.anomaly_prob_voltage,
            'anomaly_prob_power': self.anomaly_prob_power,
        }

