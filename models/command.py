"""控制指令记录（可选：用于审计/回溯）"""
from extensions import db


class DeviceCommand(db.Model):
    __tablename__ = 'device_commands'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # 目标：设备/分组
    target_type = db.Column(db.String(16), nullable=False, comment='DEVICE/GROUP')
    target_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(8), nullable=False, comment='ON/OFF')

    requested_by = db.Column(db.String(64), nullable=True, comment='操作者')
    requested_at = db.Column(db.DateTime, server_default=db.func.now(), comment='请求时间')

    # 执行结果
    result = db.Column(db.String(16), nullable=False, default='PENDING', comment='PENDING/SUCCESS/FAILED')
    error_message = db.Column(db.String(255), nullable=True)
    executed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('ix_device_commands_target', 'target_type', 'target_id'),
        db.Index('ix_device_commands_requested_at', 'requested_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'action': self.action,
            'requested_by': self.requested_by,
            'requested_at': self.requested_at.isoformat() if self.requested_at else None,
            'result': self.result,
            'error_message': self.error_message,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
        }

