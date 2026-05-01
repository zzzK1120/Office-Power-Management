"""设备模型（示例：后续可加分组、用电记录等表）"""
from extensions import db


class Device(db.Model):
    """用电设备"""
    __tablename__ = 'devices'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    office_id = db.Column(db.Integer, db.ForeignKey('offices.id', ondelete='SET NULL'), nullable=True, comment='办公室ID')
    name = db.Column(db.String(64), nullable=False, comment='设备名称')
    device_type = db.Column(db.String(64), nullable=False, default='其他', comment='设备类型')
    location = db.Column(db.String(128), default='', comment='位置/区域')
    is_on = db.Column(db.Boolean, default=False, comment='是否开启')
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    group_memberships = db.relationship(
        'DeviceGroupMember',
        back_populates='device',
        cascade='all, delete-orphan',
        lazy='selectin',
    )

    groups = db.relationship(
        'DeviceGroup',
        secondary='device_group_members',
        back_populates='devices',
        viewonly=True,
        lazy='selectin',
    )

    def to_dict(self, with_groups: bool = False):
        data = {
            'id': self.id,
            'office_id': self.office_id,
            'name': self.name,
            'device_type': self.device_type,
            'location': self.location,
            'is_on': self.is_on,
        }
        if with_groups:
            data['groups'] = [{'id': g.id, 'name': g.name} for g in self.groups]
        return data
