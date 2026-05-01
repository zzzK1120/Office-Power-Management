from datetime import datetime
import os

from flask import Flask
from sqlalchemy import inspect, text
from apscheduler.schedulers.background import BackgroundScheduler

from blueprints import register_blueprints
from config import get_config
from extensions import db
from models import Device, DeviceGroupMember, Office, Schedule
from services.power_pipeline import simulate_one_cycle
from services.smart_close import run_smart_close_checks
from services.state_log import log_device_state_change
from services.simulation_config_store import load_into_app as load_saved_simulation_config
from services.scheduler_manager import register as register_scheduler_manager
from services.office_context import get_selected_office_id

app = Flask(__name__, instance_relative_config=True)
app.config.from_object(get_config())
try:
    os.makedirs(app.instance_path, exist_ok=True)
except OSError:
    pass
load_saved_simulation_config(app)

db.init_app(app)

# 模板全局变量：当前办公室名称（用于侧边栏展示）
@app.context_processor
def inject_current_office():
    try:
        oid = get_selected_office_id()
        row = Office.query.get(int(oid))
        if not row:
            return {'current_office_name': None}
        loc = (row.location or '').strip()
        name = (row.name or '').strip()
        display = f'{loc} · {name}' if (loc and name) else (name or loc or None)
        return {'current_office_name': display}
    except Exception:
        return {'current_office_name': None}

# 注册所有蓝图（首页、监控、控制、统计、定时、报警）
register_blueprints(app)

# APScheduler 定时任务（每分钟检查一次定时策略）
scheduler = BackgroundScheduler(timezone='Asia/Shanghai')


def _test_db_connection():
    """MySQL/SQLite 连接测试（flask run 或 python app.py 都会执行）"""
    with app.app_context():
        try:
            with db.engine.connect() as conn:
                conn.execute(text("select 1"))
            print("\n========== 数据库连接成功 ==========\n", flush=True)
        except Exception as e:
            print("\n========== 数据库连接失败 ==========\n", str(e), "\n", flush=True)


def _ensure_state_logs_schema(inspector):
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    try:
        state_log_columns = inspector.get_columns('device_state_logs')
    except Exception:
        return
    if not dialect.startswith('sqlite'):
        return
    id_column = next((col for col in state_log_columns if col.get('name') == 'id'), None)
    id_type = str((id_column or {}).get('type') or '').upper()
    if 'BIGINT' not in id_type and 'BIGINTEGER' not in id_type:
        return

    try:
        print('SQLite 检测到 device_state_logs.id 为 BIGINT，准备重建表以恢复自增主键', flush=True)
        db.session.execute(text('PRAGMA foreign_keys=OFF'))
        db.session.execute(text('ALTER TABLE device_state_logs RENAME TO device_state_logs_old'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_state_logs_device_ts'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_state_logs_office_ts'))
        db.session.execute(
            text(
                """
CREATE TABLE device_state_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  office_id INTEGER,
  ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  is_on BOOLEAN NOT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'manual',
  FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE,
  FOREIGN KEY(office_id) REFERENCES offices(id) ON DELETE SET NULL
)
"""
            )
        )
        db.session.execute(
            text(
                """
INSERT INTO device_state_logs (id, device_id, office_id, ts, is_on, source)
SELECT id, device_id, office_id, ts, is_on, source FROM device_state_logs_old
"""
            )
        )
        db.session.execute(text('DROP TABLE device_state_logs_old'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_state_logs_device_ts ON device_state_logs(device_id, ts)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_state_logs_office_ts ON device_state_logs(office_id, ts)'))
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        print('SQLite device_state_logs 已重建：id 使用 INTEGER AUTOINCREMENT', flush=True)
    except Exception as exc:
        db.session.rollback()
        try:
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        except Exception:
            pass
        print('SQLite device_state_logs 重建失败：' + str(exc), flush=True)


def _ensure_command_logs_schema(inspector):
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    try:
        command_columns = inspector.get_columns('device_commands')
    except Exception:
        return
    if not dialect.startswith('sqlite'):
        return
    id_column = next((col for col in command_columns if col.get('name') == 'id'), None)
    id_type = str((id_column or {}).get('type') or '').upper()
    if 'BIGINT' not in id_type and 'BIGINTEGER' not in id_type:
        return

    try:
        print('SQLite 检测到 device_commands.id 为 BIGINT，准备重建表以恢复自增主键', flush=True)
        db.session.execute(text('PRAGMA foreign_keys=OFF'))
        db.session.execute(text('ALTER TABLE device_commands RENAME TO device_commands_old'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_device_commands_target'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_device_commands_requested_at'))
        db.session.execute(
            text(
                """
CREATE TABLE device_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_type VARCHAR(16) NOT NULL,
  target_id INTEGER NOT NULL,
  action VARCHAR(8) NOT NULL,
  requested_by VARCHAR(64),
  requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  result VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  error_message VARCHAR(255),
  executed_at DATETIME
)
"""
            )
        )
        db.session.execute(
            text(
                """
INSERT INTO device_commands (id, target_type, target_id, action, requested_by, requested_at, result, error_message, executed_at)
SELECT id, target_type, target_id, action, requested_by, requested_at, result, error_message, executed_at FROM device_commands_old
"""
            )
        )
        db.session.execute(text('DROP TABLE device_commands_old'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_device_commands_target ON device_commands(target_type, target_id)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_device_commands_requested_at ON device_commands(requested_at)'))
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        print('SQLite device_commands 已重建：id 使用 INTEGER AUTOINCREMENT', flush=True)
    except Exception as exc:
        db.session.rollback()
        try:
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        except Exception:
            pass
        print('SQLite device_commands 重建失败：' + str(exc), flush=True)


def _ensure_telemetry_schema(inspector):
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    try:
        telemetry_columns = inspector.get_columns('device_telemetry')
    except Exception:
        return
    if not dialect.startswith('sqlite'):
        return
    id_column = next((col for col in telemetry_columns if col.get('name') == 'id'), None)
    id_type = str((id_column or {}).get('type') or '').upper()
    if 'BIGINT' not in id_type and 'BIGINTEGER' not in id_type:
        return

    try:
        print('SQLite 检测到 device_telemetry.id 为 BIGINT，准备重建表以恢复自增主键', flush=True)
        db.session.execute(text('PRAGMA foreign_keys=OFF'))
        db.session.execute(text('ALTER TABLE device_telemetry RENAME TO device_telemetry_old'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_telemetry_device_ts'))
        db.session.execute(
            text(
                """
CREATE TABLE device_telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL,
  ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  voltage FLOAT,
  current FLOAT,
  power FLOAT,
  pf FLOAT,
  energy_kwh_total FLOAT,
  FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
)
"""
            )
        )
        db.session.execute(
            text(
                """
INSERT INTO device_telemetry (id, device_id, ts, voltage, current, power, pf, energy_kwh_total)
SELECT id, device_id, ts, voltage, current, power, pf, energy_kwh_total FROM device_telemetry_old
"""
            )
        )
        db.session.execute(text('DROP TABLE device_telemetry_old'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_telemetry_device_ts ON device_telemetry(device_id, ts)'))
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        print('SQLite device_telemetry 已重建：id 使用 INTEGER AUTOINCREMENT', flush=True)
    except Exception as exc:
        db.session.rollback()
        try:
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        except Exception:
            pass
        print('SQLite device_telemetry 重建失败：' + str(exc), flush=True)


def _ensure_energy_daily_schema(inspector):
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    try:
        energy_columns = inspector.get_columns('energy_daily')
    except Exception:
        return
    if not dialect.startswith('sqlite'):
        return
    id_column = next((col for col in energy_columns if col.get('name') == 'id'), None)
    id_type = str((id_column or {}).get('type') or '').upper()
    if 'BIGINT' not in id_type and 'BIGINTEGER' not in id_type:
        return

    try:
        print('SQLite 检测到 energy_daily.id 为 BIGINT，准备重建表以恢复自增主键', flush=True)
        db.session.execute(text('PRAGMA foreign_keys=OFF'))
        db.session.execute(text('ALTER TABLE energy_daily RENAME TO energy_daily_old'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_energy_daily_date'))
        db.session.execute(
            text(
                """
CREATE TABLE energy_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date DATE NOT NULL,
  device_id INTEGER,
  energy_kwh FLOAT NOT NULL,
  peak_power_w FLOAT,
  cost_estimated FLOAT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_energy_daily_date_device UNIQUE (date, device_id),
  FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE SET NULL
)
"""
            )
        )
        db.session.execute(
            text(
                """
INSERT INTO energy_daily (id, date, device_id, energy_kwh, peak_power_w, cost_estimated, created_at)
SELECT id, date, device_id, energy_kwh, peak_power_w, cost_estimated, created_at FROM energy_daily_old
"""
            )
        )
        db.session.execute(text('DROP TABLE energy_daily_old'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_energy_daily_date ON energy_daily(date)'))
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        print('SQLite energy_daily 已重建：id 使用 INTEGER AUTOINCREMENT', flush=True)
    except Exception as exc:
        db.session.rollback()
        try:
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        except Exception:
            pass
        print('SQLite energy_daily 重建失败：' + str(exc), flush=True)


def _ensure_alarms_schema(inspector):
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    try:
        alarm_columns = inspector.get_columns('alarms')
    except Exception:
        return
    if not dialect.startswith('sqlite'):
        return
    id_column = next((col for col in alarm_columns if col.get('name') == 'id'), None)
    id_type = str((id_column or {}).get('type') or '').upper()
    if 'BIGINT' not in id_type and 'BIGINTEGER' not in id_type:
        return

    try:
        print('SQLite 检测到 alarms.id 为 BIGINT，准备重建表以恢复自增主键', flush=True)
        db.session.execute(text('PRAGMA foreign_keys=OFF'))
        db.session.execute(text('ALTER TABLE alarms RENAME TO alarms_old'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_alarms_status_ts'))
        db.session.execute(text('DROP INDEX IF EXISTS ix_alarms_device_ts'))
        db.session.execute(
            text(
                """
CREATE TABLE alarms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER,
  alarm_type VARCHAR(32) NOT NULL,
  message VARCHAR(255) NOT NULL,
  ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  value FLOAT,
  threshold FLOAT,
  status VARCHAR(16) NOT NULL DEFAULT 'NEW',
  handled_at DATETIME,
  handled_by VARCHAR(64),
  FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE SET NULL
)
"""
            )
        )
        db.session.execute(
            text(
                """
INSERT INTO alarms (id, device_id, alarm_type, message, ts, value, threshold, status, handled_at, handled_by)
SELECT id, device_id, alarm_type, message, ts, value, threshold, status, handled_at, handled_by FROM alarms_old
"""
            )
        )
        db.session.execute(text('DROP TABLE alarms_old'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_alarms_status_ts ON alarms(status, ts)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_alarms_device_ts ON alarms(device_id, ts)'))
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        print('SQLite alarms 已重建：id 使用 INTEGER AUTOINCREMENT', flush=True)
    except Exception as exc:
        db.session.rollback()
        try:
            db.session.execute(text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        except Exception:
            pass
        print('SQLite alarms 重建失败：' + str(exc), flush=True)


def _ensure_schema_updates():
    """轻量补充字段，避免手动迁移时页面报错"""
    inspector = inspect(db.engine)
    _ensure_state_logs_schema(inspector)
    _ensure_command_logs_schema(inspector)
    _ensure_telemetry_schema(inspector)
    _ensure_energy_daily_schema(inspector)
    _ensure_alarms_schema(inspector)
    try:
        office_columns = {c['name'] for c in inspector.get_columns('offices')}
        if 'smart_close_enabled' not in office_columns:
            db.session.execute(
                text("ALTER TABLE offices ADD COLUMN smart_close_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
            db.session.commit()
            print('已自动补充 offices.smart_close_enabled 字段', flush=True)
    except Exception:
        db.session.rollback()

    columns = {c['name'] for c in inspector.get_columns('devices')}
    if 'device_type' not in columns:
        db.session.execute(text("ALTER TABLE devices ADD COLUMN device_type VARCHAR(64) DEFAULT '其他'"))
        db.session.commit()
        print('已自动补充 devices.device_type 字段', flush=True)
    if 'office_id' not in columns:
        db.session.execute(text("ALTER TABLE devices ADD COLUMN office_id INTEGER"))
        db.session.commit()
        print('已自动补充 devices.office_id 字段', flush=True)

    group_columns = {c['name'] for c in inspector.get_columns('device_groups')}
    if 'office_id' not in group_columns:
        db.session.execute(text("ALTER TABLE device_groups ADD COLUMN office_id INTEGER"))
        db.session.commit()
        print('已自动补充 device_groups.office_id 字段', flush=True)

    # 设备分组：由全局唯一(name)调整为办公室内唯一(office_id, name)
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    if dialect.startswith('mysql'):
        try:
            uqs = inspector.get_unique_constraints('device_groups') or []
            name_uq = None
            for uq in uqs:
                cols = [c.lower() for c in (uq.get('column_names') or [])]
                if cols == ['name']:
                    name_uq = uq.get('name')
                    break
            if name_uq:
                # MySQL: UNIQUE 约束以索引形式存在，使用 DROP INDEX
                db.session.execute(text(f"ALTER TABLE device_groups DROP INDEX `{name_uq}`"))
                db.session.commit()
                print('已移除 device_groups.name 全局唯一约束', flush=True)
        except Exception:
            # 若已移除或权限不足，忽略
            db.session.rollback()
        try:
            uqs2 = inspector.get_unique_constraints('device_groups') or []
            has_office_name = False
            for uq in uqs2:
                cols = [c.lower() for c in (uq.get('column_names') or [])]
                if cols == ['office_id', 'name'] or cols == ['name', 'office_id']:
                    has_office_name = True
                    break
            if not has_office_name:
                db.session.execute(text("ALTER TABLE device_groups ADD CONSTRAINT uq_device_groups_office_name UNIQUE (office_id, name)"))
                db.session.commit()
                print('已添加 device_groups(office_id,name) 联合唯一约束', flush=True)
        except Exception:
            db.session.rollback()
    elif dialect.startswith('sqlite'):
        # SQLite 不能直接 DROP 约束：通过重建表移除旧的 name 全局唯一，并增加联合唯一
        try:
            uqs = inspector.get_unique_constraints('device_groups') or []
            has_name_only = any(
                [c.lower() for c in (uq.get('column_names') or [])] == ['name'] for uq in uqs
            )
            uqs2 = inspector.get_unique_constraints('device_groups') or []
            has_office_name = any(
                set([c.lower() for c in (uq.get('column_names') or [])]) == {'office_id', 'name'} for uq in uqs2
            )
            if has_name_only and not has_office_name:
                print('SQLite 检测到 device_groups.name 全局唯一，准备重建表以支持办公室内唯一', flush=True)
                db.session.execute(text('PRAGMA foreign_keys=OFF'))

                # 备份旧表
                db.session.execute(text('ALTER TABLE device_groups RENAME TO device_groups_old'))
                db.session.execute(text('ALTER TABLE device_group_members RENAME TO device_group_members_old'))

                # 重建 device_groups（无 name 全局唯一）
                db.session.execute(
                    text(
                        """
CREATE TABLE device_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  office_id INTEGER,
  name VARCHAR(64) NOT NULL,
  description VARCHAR(255) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
                    )
                )
                db.session.execute(text("CREATE UNIQUE INDEX uq_device_groups_office_name ON device_groups(office_id, name)"))
                db.session.execute(text("CREATE INDEX ix_device_groups_office ON device_groups(office_id)"))

                # 重建 device_group_members
                db.session.execute(
                    text(
                        """
CREATE TABLE device_group_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  device_id INTEGER NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_device_group_member UNIQUE (group_id, device_id),
  FOREIGN KEY(group_id) REFERENCES device_groups(id) ON DELETE CASCADE,
  FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
)
"""
                    )
                )
                db.session.execute(text("CREATE INDEX ix_dgm_group_id ON device_group_members(group_id)"))
                db.session.execute(text("CREATE INDEX ix_dgm_device_id ON device_group_members(device_id)"))

                # 拷贝数据
                db.session.execute(
                    text(
                        """
INSERT INTO device_groups (id, office_id, name, description, created_at)
SELECT id, office_id, name, description, created_at FROM device_groups_old
"""
                    )
                )
                db.session.execute(
                    text(
                        """
INSERT INTO device_group_members (id, group_id, device_id, created_at)
SELECT id, group_id, device_id, created_at FROM device_group_members_old
"""
                    )
                )

                # 删除旧表
                db.session.execute(text('DROP TABLE device_group_members_old'))
                db.session.execute(text('DROP TABLE device_groups_old'))

                db.session.execute(text('PRAGMA foreign_keys=ON'))
                db.session.commit()
                print('SQLite device_groups 已重建：支持 (office_id,name) 办公室内唯一', flush=True)
        except Exception as e:
            db.session.rollback()
            try:
                db.session.execute(text('PRAGMA foreign_keys=ON'))
                db.session.commit()
            except Exception:
                pass
            print('SQLite device_groups 重建失败（将退回应用层唯一校验）：', str(e), flush=True)

    schedule_columns = {c['name'] for c in inspector.get_columns('schedules')}
    if 'office_id' not in schedule_columns:
        db.session.execute(text("ALTER TABLE schedules ADD COLUMN office_id INTEGER"))
        db.session.commit()
        print('已自动补充 schedules.office_id 字段', flush=True)
    if 'run_date' not in schedule_columns:
        db.session.execute(text("ALTER TABLE schedules ADD COLUMN run_date DATE"))
        db.session.commit()
        print('已自动补充 schedules.run_date 字段', flush=True)

    # 默认办公室与历史数据归属
    default_office = Office.query.order_by(Office.id.asc()).first()
    if not default_office:
        default_office = Office(name='默认办公室', location='', description='系统自动创建')
        db.session.add(default_office)
        db.session.commit()
        print('已自动创建默认办公室', flush=True)
    db.session.execute(
        text("UPDATE devices SET office_id=:oid WHERE office_id IS NULL"),
        {'oid': default_office.id},
    )
    db.session.execute(
        text("UPDATE device_groups SET office_id=:oid WHERE office_id IS NULL"),
        {'oid': default_office.id},
    )
    db.session.execute(
        text("UPDATE schedules SET office_id=:oid WHERE office_id IS NULL"),
        {'oid': default_office.id},
    )
    db.session.commit()


def run_schedules():
    """按分钟检查并执行定时策略（仅在主进程中运行）"""
    with app.app_context():
        now = datetime.now()
        today = now.date()
        current_time = now.time().replace(second=0, microsecond=0)
        weekday = now.isoweekday()  # 1-7 对应周一到周日

        q = Schedule.query.filter_by(enabled=True, time_of_day=current_time)

        # 按重复规则过滤
        items = []
        for s in q:
            if s.repeat_type == 'ONCE':
                if s.run_date == today:
                    items.append(s)
            elif s.repeat_type == 'EVERYDAY':
                items.append(s)
            elif s.repeat_type == 'WEEKDAY' and weekday <= 5:
                items.append(s)
            elif s.repeat_type == 'WEEKEND' and weekday >= 6:
                items.append(s)
            elif s.repeat_type == 'CUSTOM':
                days = [d.strip() for d in (s.repeat_days or '').split(',') if d.strip()]
                if str(weekday) in days:
                    items.append(s)

        if not items:
            return

        print(f"执行定时策略 {len(items)} 条", flush=True)

        for s in items:
            is_on = (s.action.upper() == 'ON')
            if s.target_type == 'DEVICE':
                d = Device.query.get(s.target_id)
                if d:
                    log_device_state_change(d, is_on, source='schedule', ts=now)
                    d.is_on = is_on
            elif s.target_type == 'GROUP':
                device_ids = [
                    r[0]
                    for r in db.session.query(DeviceGroupMember.device_id)
                    .filter(DeviceGroupMember.group_id == s.target_id)
                    .all()
                ]
                if device_ids:
                    devices = Device.query.filter(Device.id.in_(device_ids)).all()
                    for d in devices:
                        log_device_state_change(d, is_on, source='schedule', ts=now)
                        d.is_on = is_on
            elif s.target_type == 'COLLECTION':
                devices = Device.query.filter(Device.office_id == s.target_id).all()
                scope_name = (s.name or '').strip()
                if '照明' in scope_name or '灯' in scope_name:
                    devices = [d for d in devices if any(keyword in (d.device_type or '') or keyword in (d.name or '') or keyword in (d.location or '') for keyword in ['照明', '灯', '灯光'])]
                elif '空调' in scope_name:
                    devices = [d for d in devices if '空调' in (d.device_type or '') or '空调' in (d.name or '') or '空调' in (d.location or '')]
                elif '插座' in scope_name:
                    devices = [d for d in devices if '插座' in (d.device_type or '') or '插座' in (d.name or '') or '插座' in (d.location or '')]
                for d in devices:
                    log_device_state_change(d, is_on, source='schedule', ts=now)
                    d.is_on = is_on
            if s.repeat_type == 'ONCE':
                s.enabled = False

        db.session.commit()


def run_simulation():
    """按配置的间隔（默认每秒）模拟一轮实时数据，打通监控/统计/报警链路"""
    with app.app_context():
        simulate_one_cycle()


def run_smart_close():
    """按间隔检查智能关闭：个性化关断时间 + 同办公室联动"""
    with app.app_context():
        run_smart_close_checks()


# 导入模型，使 db.create_all() 能发现所有表（放在 db.init_app 之后）
import models  # noqa: E402, F401


# 应用一加载就测一次库、建表，并启动调度器（仅在主进程启动一次）
with app.app_context():
    _test_db_connection()
    db.create_all()
    _ensure_schema_updates()

    # 避免 Werkzeug 重载「父进程」启动调度器；直接 python app.py（__main__）时仍需启动
    def _should_start_apscheduler():
        if not app.debug:
            return True
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            return True
        return __name__ == '__main__'

    if _should_start_apscheduler():
        if not scheduler.running:
            sample_interval = int(app.config.get('SIM_SAMPLE_INTERVAL_SECONDS', 1))
            scheduler.add_job(run_schedules, 'interval', minutes=1, id='run_schedules')
            scheduler.add_job(run_simulation, 'interval', seconds=sample_interval, id='run_simulation')
            scheduler.add_job(run_smart_close, 'interval', minutes=2, id='run_smart_close')
            scheduler.start()
            register_scheduler_manager(app, scheduler)
            print(
                f'APScheduler 已启动：每分钟检查定时策略、每{sample_interval}秒生成模拟数据、每2分钟智能关闭巡检',
                flush=True,
            )


if __name__ == '__main__':
    app.run(debug=app.config.get('DEBUG', True))
