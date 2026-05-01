"""蓝图包：统一注册所有蓝图"""
from flask import Flask

from .home import bp as home_bp
from .monitor import bp as monitor_bp
from .control import bp as control_bp
from .statistics import bp as statistics_bp
from .schedule import bp as schedule_bp
from .alarm import bp as alarm_bp
from .agent import bp as agent_bp


def register_blueprints(app: Flask) -> None:
    """把各功能模块的蓝图注册到 app 上"""
    app.register_blueprint(home_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(control_bp)
    app.register_blueprint(statistics_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(alarm_bp)
    app.register_blueprint(agent_bp)
