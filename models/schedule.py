"""定时策略（上班/下班自动开关）"""
from extensions import db


class Schedule(db.Model):
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    office_id = db.Column(db.Integer, db.ForeignKey('offices.id', ondelete='SET NULL'), nullable=True, comment='办公室ID')
    name = db.Column(db.String(64), nullable=False, comment='策略名称')

    # 目标：设备、分组或办公室范围
    target_type = db.Column(db.String(16), nullable=False, comment='DEVICE/GROUP/COLLECTION')
    target_id = db.Column(db.Integer, nullable=False, comment='设备ID、分组ID或办公室ID')

    # 动作：ON / OFF
    action = db.Column(db.String(8), nullable=False, comment='ON/OFF')

    # 每天的执行时间：HH:MM:SS（数据库 Time 类型）
    time_of_day = db.Column(db.Time, nullable=False, comment='执行时间')

    # 重复规则：ONCE / EVERYDAY / WEEKDAY / WEEKEND / CUSTOM
    repeat_type = db.Column(db.String(16), nullable=False, default='ONCE')
    # 自定义时可用：用逗号保存 1-7（周一到周日）例如 "1,2,3,4,5"
    repeat_days = db.Column(db.String(32), default='', comment='自定义重复天')
    run_date = db.Column(db.Date, nullable=True, comment='单次执行日期')

    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.Index('ix_schedules_enabled_time', 'enabled', 'time_of_day'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'office_id': self.office_id,
            'name': self.name,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'action': self.action,
            'time_of_day': self.time_of_day.isoformat() if self.time_of_day else None,
            'repeat_type': self.repeat_type,
            'repeat_days': self.repeat_days,
            'run_date': self.run_date.isoformat() if self.run_date else None,
            'enabled': self.enabled,
        }

