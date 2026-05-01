"""
扩展实例：在 app 创建后通过 init_app 绑定，避免循环导入。
模型和蓝图中通过 from extensions import db 使用。
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
