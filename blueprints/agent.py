"""AI 助手蓝图：页面与查询接口。"""
from flask import Blueprint, jsonify, render_template, request

from services.agent_control_service import confirm_pending_device_control
from services.agent_schedule_service import confirm_pending_device_schedule
from services.agent_service import handle_chat_message

bp = Blueprint('agent', __name__, url_prefix='/agent')


@bp.route('/')
def index():
    """AI 助手页面。"""
    return render_template('agent.html')


@bp.post('/api/chat')
def api_chat():
    payload = request.get_json(silent=True) or {}
    message = (payload.get('message') or '').strip()
    if not message:
        return jsonify({'ok': False, 'error': '请输入一条指令。'}), 400
    try:
        return jsonify(handle_chat_message(message))
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'ok': False, 'error': 'AI 助手查询失败，请稍后重试。'}), 500


@bp.post('/api/actions/confirm')
def api_confirm_action():
    payload = request.get_json(silent=True) or {}
    pending_action = payload.get('pendingAction') or {}
    if not isinstance(pending_action, dict):
        return jsonify({'ok': False, 'error': '待确认操作格式无效。'}), 400
    kind = (pending_action.get('kind') or '').strip()
    if kind not in {'device_control', 'device_schedule'}:
        return jsonify({'ok': False, 'error': '当前仅支持确认设备控制或定时策略操作。'}), 400
    try:
        if kind == 'device_schedule':
            return jsonify(confirm_pending_device_schedule(pending_action))
        return jsonify(confirm_pending_device_control(pending_action))
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'ok': False, 'error': 'AI 助手执行失败，请稍后重试。'}), 500
