"""APScheduler 与 Flask app 绑定，供蓝图内重调度模拟任务。"""
from __future__ import annotations

_scheduler = None
_flask_app = None


def register(app, scheduler) -> None:
    global _scheduler, _flask_app
    _scheduler = scheduler
    _flask_app = app


def reschedule_simulation_job() -> None:
    """修改 SIM_SAMPLE_INTERVAL_SECONDS 后调用，使定时任务间隔立即生效。"""
    if _scheduler is None or _flask_app is None or not _scheduler.running:
        return
    sec = max(1, int(_flask_app.config.get('SIM_SAMPLE_INTERVAL_SECONDS', 1)))
    try:
        _scheduler.reschedule_job('run_simulation', trigger='interval', seconds=sec)
    except Exception:
        pass
