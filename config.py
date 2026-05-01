"""
应用配置：开发 / 生产 两套
通过环境变量 FLASK_ENV 切换（不设则默认开发）。
"""
import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {'0', 'false', 'no', 'off'}


class BaseConfig:
    """共用配置"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    # ===== 模拟采样参数（可用于答辩演示） =====
    SIM_SAMPLE_INTERVAL_SECONDS = int(os.environ.get('SIM_SAMPLE_INTERVAL_SECONDS', 1))
    SIM_VOLTAGE_MIN = float(os.environ.get('SIM_VOLTAGE_MIN', 212.0))
    SIM_VOLTAGE_MAX = float(os.environ.get('SIM_VOLTAGE_MAX', 228.0))
    SIM_POWER_MIN = float(os.environ.get('SIM_POWER_MIN', 120.0))
    SIM_POWER_MAX = float(os.environ.get('SIM_POWER_MAX', 1800.0))
    SIM_STANDBY_POWER_MAX = float(os.environ.get('SIM_STANDBY_POWER_MAX', 5.0))
    # 异常概率（0~1）
    SIM_ANOMALY_PROB_VOLTAGE = float(os.environ.get('SIM_ANOMALY_PROB_VOLTAGE', 0.04))
    SIM_ANOMALY_PROB_POWER = float(os.environ.get('SIM_ANOMALY_PROB_POWER', 0.04))
    DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    DEEPSEEK_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').strip()
    DEEPSEEK_MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat').strip()
    DEEPSEEK_TIMEOUT_SECONDS = float(os.environ.get('DEEPSEEK_TIMEOUT_SECONDS', 20))
    AGENT_QUERY_DEFAULT_RANGE_DAYS = _env_int('AGENT_QUERY_DEFAULT_RANGE_DAYS', 30)
    AGENT_USE_LANGCHAIN = _env_bool('AGENT_USE_LANGCHAIN', True)
    AGENT_LANGCHAIN_MAX_TOOL_CALLS = _env_int('AGENT_LANGCHAIN_MAX_TOOL_CALLS', 4)


class DevelopmentConfig(BaseConfig):
    """开发环境：本机调试，用 SQLite，无需 MySQL"""
    DEBUG = True
    # 数据库：SQLite，文件在项目根目录
    SQLALCHEMY_DATABASE_URI = 'sqlite:///dev.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def _mysql_uri():
    """从环境变量拼 MySQL 连接地址（开发/生产共用）"""
    host = os.environ.get('MYSQL_HOST', '127.0.0.1')
    port = int(os.environ.get('MYSQL_PORT', 3306))
    user = os.environ.get('MYSQL_USER', 'root')
    password = os.environ.get('MYSQL_PASSWORD', '')
    database = os.environ.get('MYSQL_DATABASE', 'sql')
    return f'mysql+mysqldb://{user}:{password}@{host}:{port}/{database}'


class DevelopmentMySQLConfig(BaseConfig):
    """开发环境 + MySQL：从环境变量读连接信息"""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or _mysql_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class DevelopmentMySQLDirectConfig(BaseConfig):
    """
    开发环境 + MySQL：直接在下面改连接信息，无需设环境变量。
    驱动：mysqlclient（mysql+mysqldb）。
    """
    DEBUG = True
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 直接在这里填写
    HOSTNAME = "127.0.0.1"
    PORT = 3306
    USERNAME = "root"
    PASSWORD = "123456"
    DATABASE = "sql"

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+mysqldb://{USERNAME}:{PASSWORD}@{HOSTNAME}:{PORT}/{DATABASE}?charset=utf8mb4"
    )


class ProductionConfig(BaseConfig):
    """生产环境：正式部署，用 MySQL"""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or _mysql_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False


# 根据 FLASK_ENV 选哪套配置（不设则用开发 SQLite）
config_map = {
    'development': DevelopmentConfig,
    'development_mysql': DevelopmentMySQLConfig,
    'development_mysql_direct': DevelopmentMySQLDirectConfig,  # 直接写连接信息，无需环境变量
    'production': ProductionConfig,
}


def get_config():
    env = os.environ.get('FLASK_ENV', 'development')
    return config_map.get(env, DevelopmentConfig)
