"""AI 助手查询编排服务。"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from urllib import error, request

from flask import current_app

from services.agent_control_service import (
    ControlResolutionError,
    build_pending_device_control,
    is_probably_device_control,
)
from services.agent_langchain_service import handle_chat_message_with_langchain
from services.agent_query_service import (
    find_unclosed_devices,
    get_device_energy_report,
    get_office_energy_report,
    normalize_date_range,
    resolve_office_by_name,
    summarize_top_office_in_range,
)
from services.agent_schedule_service import build_pending_device_schedule, is_probably_device_schedule

QUERY_INTENTS = {
    'check_unclosed_devices',
    'compare_offices_energy',
    'office_energy_report',
    'device_energy_report',
}

UNSUPPORTED_INTENTS = {
    'meeting_schedule': '当前仅开放查询和设备控制能力，会议预约将在下一阶段接入。',
}


UNKNOWN_INTENT_REPLY = '我暂时没准确理解你的问题。你可以直接问：最近30天哪个办公室用电最多、默认办公室最近7天用电情况、还有哪些设备没关，或者说“关闭前台空调”“打开默认办公室所有设备”。'


GREETING_REPLY = '你好，我是办公室智能用电助手。你可以问我最近30天哪个办公室用电最多、默认办公室最近7天用电情况，或者还有哪些设备没关。'
CAPABILITY_REPLY = '我目前可以帮你查询办公室和设备的用电情况，也可以在你确认后执行单设备、设备分组或办公室范围的开关控制；会议预约还没有开放。'


def _build_smalltalk_response(message: str) -> dict | None:
    text = (message or '').strip()
    compact = re.sub(r'\s+', '', text).lower()
    if not compact:
        return None

    greetings = {'你好', '您好', 'hello', 'hi', '嗨', '哈喽', '在吗', '在不在', '有人吗', '你好呀'}
    capability_keywords = ['你是谁', '你是干嘛的', '你能做什么', '你会什么', '怎么用', '可以帮我做什么']
    thanks = {'谢谢', '谢谢你', '多谢', 'thanks', 'thankyou'}

    if compact in greetings:
        return {
            'ok': True,
            'type': 'text',
            'intent': 'smalltalk_greeting',
            'source': 'builtin',
            'reply': GREETING_REPLY,
        }

    if any(keyword in compact for keyword in capability_keywords):
        return {
            'ok': True,
            'type': 'text',
            'intent': 'smalltalk_capability',
            'source': 'builtin',
            'reply': CAPABILITY_REPLY,
        }

    if compact in thanks:
        return {
            'ok': True,
            'type': 'text',
            'intent': 'smalltalk_thanks',
            'source': 'builtin',
            'reply': '不客气，你可以继续直接问我用电查询问题。',
        }

    return None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _extract_date_range_from_text(message: str) -> tuple[date | None, date | None]:
    today = date.today()
    text = (message or '').strip()
    if not text:
        return None, None

    date_matches = re.findall(r'(\d{4}-\d{1,2}-\d{1,2})', text)
    if len(date_matches) >= 2:
        start = _parse_iso_date(date_matches[0])
        end = _parse_iso_date(date_matches[1])
        if start and end:
            return start, end
    if len(date_matches) == 1:
        dt = _parse_iso_date(date_matches[0])
        if dt:
            return dt, dt

    if '最近7天' in text:
        return today - timedelta(days=6), today
    if '最近30天' in text:
        return today - timedelta(days=29), today
    if '本月' in text:
        return date(today.year, today.month, 1), today
    if '今天' in text:
        return today, today
    if '昨天' in text:
        y = today - timedelta(days=1)
        return y, y
    return None, None


def _fallback_parse_query_intent(message: str) -> dict:
    text = (message or '').strip()
    start, end = _extract_date_range_from_text(text)
    office = resolve_office_by_name(text)
    office_name = office.name if office else None

    if any(keyword in text for keyword in ['开会', '会议', '预约']):
        return {'intent': 'meeting_schedule'}
    if is_probably_device_control(text):
        return {'intent': 'device_control'}

    if any(keyword in text for keyword in ['最多', '最高', '排行', '排名']) and '办公室' in text:
        return {
            'intent': 'compare_offices_energy',
            'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None,
        }

    if any(keyword in text for keyword in ['哪个办公室', '哪间办公室']) and any(keyword in text for keyword in ['用电', '耗电', '电费']):
        return {
            'intent': 'compare_offices_energy',
            'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None,
        }

    if '设备' in text and any(keyword in text for keyword in ['报表', '耗电', '用电', '统计', '最多']):
        return {
            'intent': 'device_energy_report',
            'office_name': office_name,
            'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None,
        }

    if office_name and any(keyword in text for keyword in ['报表', '耗电', '用电', '统计', '情况']):
        return {
            'intent': 'office_energy_report',
            'office_name': office_name,
            'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None,
        }

    if any(keyword in text for keyword in ['没关', '未关', '还开着', '还有哪些', '哪些设备还开着', '哪些灯还开着', '检查']) and any(keyword in text for keyword in ['灯', '照明', '设备', '电器', '空调', '办公室', '未关闭']):
        device_type = '照明' if any(keyword in text for keyword in ['灯', '照明', '灯光']) else None
        return {
            'intent': 'check_unclosed_devices',
            'office_name': office_name,
            'device_type': device_type,
        }

    return {'intent': 'unknown'}


def _call_deepseek_parser(message: str) -> dict | None:
    api_key = (current_app.config.get('DEEPSEEK_API_KEY') or '').strip()
    if not api_key:
        return None

    base_url = (current_app.config.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/')
    model = current_app.config.get('DEEPSEEK_MODEL') or 'deepseek-chat'
    timeout_seconds = float(current_app.config.get('DEEPSEEK_TIMEOUT_SECONDS', 20))

    system_prompt = (
        '你是办公室智能用电系统的查询意图识别器。'
        '你只能输出单个 JSON 对象，不要输出 markdown，不要解释。'
        '支持 intent: check_unclosed_devices, compare_offices_energy, office_energy_report, device_energy_report, device_control, meeting_schedule。'
        '字段只允许: intent, office_name, device_type, start, end。'
        '日期必须是 YYYY-MM-DD；未知字段填 null。'
    )
    body = {
        'model': model,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': message},
        ],
    }
    req = request.Request(
        url=base_url + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    choices = payload.get('choices') or []
    if not choices:
        return None
    content = (((choices[0] or {}).get('message') or {}).get('content') or '').strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def parse_query_intent(message: str) -> dict:
    parsed = _call_deepseek_parser(message)
    source = 'deepseek'
    intent = ((parsed or {}).get('intent') or '').strip() if isinstance(parsed, dict) else ''
    if not intent:
        parsed = _fallback_parse_query_intent(message)
        source = 'rule'
    parsed['_source'] = source
    parsed['_message'] = message
    return parsed


def normalize_query_command(command: dict) -> dict:
    intent = (command.get('intent') or '').strip()
    if not intent:
        raise ValueError('未识别出查询意图，请换一种说法再试试。')

    if intent in UNSUPPORTED_INTENTS:
        return {'intent': intent}
    if intent == 'device_control':
        return {
            'intent': intent,
            'message': command.get('_message') or '',
        }
    if intent == 'unknown':
        return {'intent': 'unknown'}
    if intent not in QUERY_INTENTS:
        raise ValueError('当前只支持查询类能力。')

    inferred_start, inferred_end = _extract_date_range_from_text(command.get('_message') or '')
    start = inferred_start or _parse_iso_date(command.get('start'))
    end = inferred_end or _parse_iso_date(command.get('end'))
    default_days = int(current_app.config.get('AGENT_QUERY_DEFAULT_RANGE_DAYS', 30) or 30)
    start, end = normalize_date_range(start, end, default_days=default_days)

    normalized = {
        'intent': intent,
        'start': start,
        'end': end,
        'office_name': (command.get('office_name') or '').strip() or None,
        'device_type': (command.get('device_type') or '').strip() or None,
    }

    if intent == 'office_energy_report' and not normalized['office_name']:
        raise ValueError('请说明要查询哪个办公室。')

    return normalized


def execute_query_command(command: dict) -> dict:
    intent = command['intent']
    if intent == 'device_control':
        raise ValueError('设备控制需要先生成待确认操作，不能走查询执行链路。')
    if intent == 'check_unclosed_devices':
        return find_unclosed_devices(command.get('office_name'), command.get('device_type'))
    if intent == 'compare_offices_energy':
        return summarize_top_office_in_range(command['start'], command['end'])
    if intent == 'office_energy_report':
        office = resolve_office_by_name(command.get('office_name'))
        if not office:
            raise ValueError('没有找到对应的办公室，请检查名称后再试。')
        return get_office_energy_report(int(office.id), command['start'], command['end'])
    if intent == 'device_energy_report':
        office = resolve_office_by_name(command.get('office_name')) if command.get('office_name') else None
        return get_device_energy_report(command['start'], command['end'], office_id=int(office.id) if office else None)
    raise ValueError('当前只支持查询类能力。')


def _build_unclosed_reply(result: dict) -> str:
    summary = result.get('summary') or {}
    office_count = int(summary.get('office_count') or 0)
    device_count = int(summary.get('device_count') or 0)
    if device_count == 0:
        return '当前没有发现未关闭的设备。'
    top_office = (result.get('offices') or [{}])[0]
    top_name = top_office.get('display_name') or top_office.get('name') or '某办公室'
    return f'当前发现 {office_count} 个办公室还有设备未关闭，共 {device_count} 台；其中 {top_name} 未关闭设备最多。'


def _build_compare_reply(result: dict) -> str:
    top_office = result.get('top_office')
    if not top_office:
        return '所选时间范围内还没有可用的用电统计数据。'
    start = result.get('start')
    end = result.get('end')
    return (
        f'{start} 到 {end} 期间，用电最多的是 {top_office.get("display_name") or top_office.get("office_name")}'
        f'，总用电 {top_office.get("energy_kwh", 0)} kWh，占比 {top_office.get("share_pct", 0)}%。'
    )


def _build_office_reply(result: dict) -> str:
    summary = result.get('summary') or {}
    return (
        f'{result.get("office_name")} 在 {result.get("start")} 到 {result.get("end")} '
        f'总用电 {summary.get("total_kwh", 0)} kWh，估算电费 {summary.get("total_cost", 0)} 元。'
    )


def _build_device_reply(result: dict) -> str:
    top_device = result.get('top_device')
    if not top_device:
        return '所选时间范围内没有找到设备级用电数据。'
    scope = result.get('office_name') or '全部办公室'
    return (
        f'{scope} 在 {result.get("start")} 到 {result.get("end")} 期间，'
        f'最耗电设备是 {top_device.get("name")}，总用电 {top_device.get("energy_kwh", 0)} kWh。'
    )


def build_query_response(command: dict, result: dict) -> dict:
    intent = command['intent']
    if intent == 'check_unclosed_devices':
        reply = _build_unclosed_reply(result)
    elif intent == 'compare_offices_energy':
        reply = _build_compare_reply(result)
    elif intent == 'office_energy_report':
        reply = _build_office_reply(result)
    else:
        reply = _build_device_reply(result)
    return {
        'ok': True,
        'type': 'query_result',
        'intent': intent,
        'reply': reply,
        'result': result,
    }


def handle_chat_message(message: str, session_id: str | None = None) -> dict:
    text = (message or '').strip()
    if not text:
        raise ValueError('请输入一条指令。')

    smalltalk_response = _build_smalltalk_response(text)
    if smalltalk_response is not None:
        return smalltalk_response

    if is_probably_device_schedule(text):
        try:
            return build_pending_device_schedule(text)
        except ControlResolutionError as exc:
            return {
                'ok': True,
                'type': 'text',
                'intent': 'device_schedule',
                'source': 'schedule_rule',
                'reply': str(exc),
            }

    if is_probably_device_control(text):
        try:
            return build_pending_device_control(text)
        except ControlResolutionError as exc:
            return {
                'ok': True,
                'type': 'text',
                'intent': 'device_control',
                'source': 'control_rule',
                'reply': str(exc),
            }

    if bool(current_app.config.get('AGENT_USE_LANGCHAIN', True)):
        langchain_response = handle_chat_message_with_langchain(text, session_id=session_id)
        if langchain_response is not None:
            return langchain_response

    parsed = parse_query_intent(text)
    command = normalize_query_command(parsed)
    source = parsed.get('_source', 'unknown')
    if command['intent'] in UNSUPPORTED_INTENTS:
        return {
            'ok': True,
            'type': 'text',
            'intent': command['intent'],
            'source': source,
            'reply': UNSUPPORTED_INTENTS[command['intent']],
        }

    if command['intent'] == 'unknown':
        return {
            'ok': True,
            'type': 'text',
            'intent': 'unknown',
            'source': source,
            'reply': UNKNOWN_INTENT_REPLY,
        }

    result = execute_query_command(command)
    response = build_query_response(command, result)
    response['source'] = source
    return response
