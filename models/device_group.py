"""设备分组与关联（多对多）"""
from extensions import db


class DeviceGroup(db.Model):
    """设备分组"""
    __tablename__ = 'device_groups'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    office_id = db.Column(db.Integer, db.ForeignKey('offices.id', ondelete='SET NULL'), nullable=True, comment='办公室ID')
    name = db.Column(db.String(64), nullable=False, comment='分组名称（办公室内唯一）')
    description = db.Column(db.String(255), default='', comment='分组描述')
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('office_id', 'name', name='uq_device_groups_office_name'),
        db.Index('ix_device_groups_office', 'office_id'),
    )

    members = db.relationship(
        'DeviceGroupMember',
        back_populates='group',
        cascade='all, delete-orphan',
        lazy='selectin',
    )

    devices = db.relationship(
        'Device',
        secondary='device_group_members',
        back_populates='groups',
        viewonly=True,
        lazy='selectin',
    )

    def to_dict(self, with_devices: bool = False):
        data = {
            'id': self.id,
            'office_id': self.office_id,
            'name': self.name,
            'description': self.description,
        }
        if with_devices:
            data['devices'] = [d.to_dict() for d in self.devices]
        return data


class DeviceGroupMember(db.Model):
    """设备-分组关联表（多对多）"""
    __tablename__ = 'device_group_members'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    group_id = db.Column(db.Integer, db.ForeignKey('device_groups.id', ondelete='CASCADE'), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('group_id', 'device_id', name='uq_device_group_member'),
        db.Index('ix_dgm_group_id', 'group_id'),
        db.Index('ix_dgm_device_id', 'device_id'),
    )

    group = db.relationship('DeviceGroup', back_populates='members', lazy='joined')
    device = db.relationship('Device', back_populates='group_memberships', lazy='joined')

