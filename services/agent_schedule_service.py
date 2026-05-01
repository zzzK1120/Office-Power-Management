"""AI 助手定时开关编排与创建服务。"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from extensions import db
from models import Device, DeviceGroup, Schedule
from services.agent_control_service import (
    ACTION_LABELS,
    ControlResolutionError,
    resolve_collection_target,
    resolve_control_target,
)
from services.office_context import get_selected_office_id

REPEAT_LABELS = {
    'ONCE': '仅一次',
    'EVERYDAY': '每天',
    'WEEKDAY': '工作日',
    'WEEKEND': '周末',
    'CUSTOM': '自定义',
}
WEEKDAY_MAP = {
    '一': '1',
    '二': '2',
    '三': '3',
    '四': '4',
    '五': '5',
    '六': '6',
    '日': '7',
    '天': '7',
}
CN_NUM_MAP = {
    '零': 0,
    '一': 1,
    '二': 2,
    '两': 2,
    '三': 3,
    '四': 4,
    '五': 5,
    '六': 6,
    '七': 7,
    '八': 8,
    '九': 9,
    '十': 10,
}
SCHEDULE_HINTS = ['每天', '工作日', '周末', '每周', '定时', '每天早上', '每天晚上', '今天', '明天', '下午', '上午', '晚上', '早上', '中午']


def is_probably_device_schedule(message: str) -> bool:
    text = (message or '').strip()
    if not text:
        return False
    has_action = any(token in text for token in ['打开', '开启', '启动', '关闭', '关掉', '关上', '停掉', '断开'])
    if not has_action:
        return False
    if any(hint in text for hint in SCHEDULE_HINTS):
        return True
    if re.search(r'(?<!\d)([01]?\d|2[0-3]):[0-5]\d(?!\d)', text):
        return True
    return bool(re.search(r'(上午|中午|下午|晚上|早上)?\s*([01]?\d|2[0-3])点(半|[一二三四五六七八九]?刻|[0-5]?\d分)?', text))


def _extract_action(message: str) -> str:
    text = (message or '').strip()
    for action, label in ACTION_LABELS.items():
        if label in text:
            return action
    if any(token in text for token in ['开启', '启动']):
        return 'ON'
    if any(token in text for token in ['关掉', '关上', '停掉', '断开']):
        return 'OFF'
    raise ControlResolutionError('我需要知道你是想定时打开还是定时关闭某个设备、分组或办公室范围设备。')


def _parse_chinese_hour(value: str) -> int:
    text = str(value or '').strip()
    if not text:
        raise ValueError('empty hour')
    if text.isdigit():
        return int(text)
    if text == '十':
        return 10
    if text.startswith('十'):
        return 10 + CN_NUM_MAP.get(text[1:], 0)
    if text.endswith('十'):
        return CN_NUM_MAP.get(text[0], 0) * 10
    if '十' in text:
        left, right = text.split('十', 1)
        return CN_NUM_MAP.get(left, 0) * 10 + CN_NUM_MAP.get(right, 0)
    if text in CN_NUM_MAP:
        return CN_NUM_MAP[text]
    raise ValueError('invalid chinese hour')


def _extract_time(message: str) -> str:
    text = str(message or '').replace('：', ':')
    match = re.search(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)', text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    cn_match = re.search(r'(上午|中午|下午|晚上|早上)?\s*([零一二三四五六七八九十两\d]{1,3})点(半|一刻|三刻|[0-5]?\d分)?', text)
    if not cn_match:
        raise ControlResolutionError('请用固定时刻描述定时策略，例如“每天 18:00 关闭前台空调”或“下午五点打开默认办公室所有设备”。')

    period = cn_match.group(1) or ''
    hour_text = cn_match.group(2)
    minute_text = cn_match.group(3) or ''
    try:
        hour = _parse_chinese_hour(hour_text)
    except ValueError:
        raise ControlResolutionError('请提供有效的固定执行时间。')
    minute = 0

    if minute_text == '半':
        minute = 30
    elif minute_text == '一刻':
        minute = 15
    elif minute_text == '三刻':
        minute = 45
    elif minute_text.endswith('分'):
        minute = int(minute_text[:-1])

    if period in {'下午', '晚上'} and hour < 12:
        hour += 12
    elif period == '中午' and hour < 11:
        hour += 12
    elif period in {'上午', '早上'} and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        raise ControlResolutionError('请提供有效的固定执行时间。')
    return f'{hour:02d}:{minute:02d}'


def _extract_schedule_date(message: str) -> date:
    text = str(message or '').strip()
    today = date.today()
    iso_match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            pass
    if '明天' in text:
        return today + timedelta(days=1)
    if '后天' in text:
        return today + timedelta(days=2)
    return today


def _extract_repeat(message: str) -> tuple[str, str, str]:
    text = str(message or '')
    if '工作日' in text:
        return 'WEEKDAY', '', '工作日'
    if '周末' in text:
        return 'WEEKEND', '', '周末'
    week_match = re.search(r'每周([一二三四五六日天,，、\s]+)', text)
    if week_match:
        values = []
        seen = set()
        for char in week_match.group(1):
            day = WEEKDAY_MAP.get(char)
            if day and day not in seen:
                seen.add(day)
                values.append(day)
        if values:
            label = '每周' + '、'.join([key for key, value in WEEKDAY_MAP.items() if value in values and key != '天'])
            return 'CUSTOM', ','.join(values), label
    return 'ONCE', '', '仅一次'


def _build_schedule_name(repeat_label: str, time_text: str, action: str, target_name: str) -> str:
    return f'{repeat_label}{time_text}{ACTION_LABELS[action]}{target_name}'[:64]


def build_pending_device_schedule(message: str) -> dict:
    action = _extract_action(message)
    time_text = _extract_time(message)
    run_date = _extract_schedule_date(message)
    repeat_type, repeat_days, repeat_label = _extract_repeat(message)

    collection_target = resolve_collection_target(message)
    if collection_target is not None:
        schedule_name = _build_schedule_name(
            repeat_label,
            time_text,
            action,
            f"{collection_target['office_name']}{collection_target['device_type_label']}",
        )
        pending_action = {
            'kind': 'device_schedule',
            'kind_label': '待确认定时策略',
            'scope': 'device_collection',
            'action': action,
            'action_label': ACTION_LABELS[action],
            'office_id': collection_target['office_id'],
            'office_name': collection_target['office_name'],
            'device_type': collection_target['device_type'],
            'device_type_label': collection_target['device_type_label'],
            'device_ids': collection_target['device_ids'],
            'device_count': collection_target['device_count'],
            'sample_devices': collection_target['sample_devices'],
            'time': time_text,
            'run_date': run_date.isoformat(),
            'repeat_type': repeat_type,
            'repeat_days': repeat_days,
            'repeat_label': repeat_label,
            'name': schedule_name,
            'summary': (
                f'即将创建定时策略：{run_date.isoformat()} {time_text} '
                f'{ACTION_LABELS[action]}{collection_target["office_name"]}的{collection_target["device_type_label"]}'
            ),
            'details': [
                {'label': '策略名称', 'value': schedule_name},
                {'label': '执行日期', 'value': run_date.isoformat()},
                {'label': '执行时间', 'value': time_text},
                {'label': '重复规则', 'value': repeat_label},
                {'label': '操作', 'value': ACTION_LABELS[action]},
                {'label': '办公室', 'value': collection_target['office_name']},
                {'label': '范围', 'value': collection_target['device_type_label']},
                {'label': '命中设备', 'value': f"{collection_target['device_count']} 台"},
            ],
        }
        if collection_target['sample_devices']:
            pending_action['details'].append({'label': '示例设备', 'value': '、'.join(collection_target['sample_devices'])})
        return {
            'ok': True,
            'type': 'pending_action',
            'intent': 'device_schedule',
            'source': 'schedule_rule',
            'reply': f'我已识别到你的定时开关需求，准备创建“{schedule_name}”。默认按单次策略处理，请确认执行。',
            'pendingAction': pending_action,
        }

    target = resolve_control_target(message, target_type_style='schedule')
    target_label = '分组' if target['target_type'] == 'GROUP' else '设备'
    schedule_name = _build_schedule_name(repeat_label, time_text, action, target['target_name'])
    pending_action = {
        'kind': 'device_schedule',
        'kind_label': '待确认定时策略',
        'action': action,
        'action_label': ACTION_LABELS[action],
        'target_type': target['target_type'],
        'target_type_label': target_label,
        'target_id': target['target_id'],
        'target_name': target['target_name'],
        'time': time_text,
        'run_date': run_date.isoformat(),
        'repeat_type': repeat_type,
        'repeat_days': repeat_days,
        'repeat_label': repeat_label,
        'name': schedule_name,
        'summary': f'即将创建定时策略：{run_date.isoformat()} {time_text} {ACTION_LABELS[action]}{target_label}“{target["target_name"]}”',
        'details': [
            {'label': '策略名称', 'value': schedule_name},
            {'label': '执行日期', 'value': run_date.isoformat()},
            {'label': '执行时间', 'value': time_text},
            {'label': '重复规则', 'value': repeat_label},
            {'label': '操作', 'value': ACTION_LABELS[action]},
            {'label': '目标类型', 'value': target_label},
            {'label': '目标名称', 'value': target['target_name']},
        ],
    }
    return {
        'ok': True,
        'type': 'pending_action',
        'intent': 'device_schedule',
        'source': 'schedule_rule',
        'reply': f'我已识别到你的定时开关需求，准备创建“{schedule_name}”。默认按单次策略处理，请确认执行。',
        'pendingAction': pending_action,
    }


def confirm_pending_device_schedule(payload: dict) -> dict:
    name = str((payload or {}).get('name') or '').strip()
    action = str((payload or {}).get('action') or '').strip().upper()
    scope = str((payload or {}).get('scope') or '').strip().lower()
    target_type = str((payload or {}).get('target_type') or '').strip().upper()
    target_name = str((payload or {}).get('target_name') or '').strip()
    time_text = str((payload or {}).get('time') or '').strip()
    run_date_text = str((payload or {}).get('run_date') or '').strip()
    repeat_type = str((payload or {}).get('repeat_type') or '').strip().upper() or 'ONCE'
    repeat_days = str((payload or {}).get('repeat_days') or '').strip()

    if not name:
        raise ValueError('待确认策略缺少名称。')
    if action not in {'ON', 'OFF'}:
        raise ValueError('待确认策略缺少有效的控制动作。')
    try:
        time_of_day = datetime.strptime(time_text, '%H:%M').time()
    except Exception as exc:
        raise ValueError('待确认策略缺少有效的执行时间。') from exc
    try:
        run_date = date.fromisoformat(run_date_text) if run_date_text else date.today()
    except ValueError as exc:
        raise ValueError('待确认策略缺少有效的执行日期。') from exc
    if repeat_type not in {'ONCE', 'EVERYDAY', 'WEEKDAY', 'WEEKEND', 'CUSTOM'}:
        raise ValueError('待确认策略缺少有效的重复规则。')
    if repeat_type == 'CUSTOM' and not repeat_days:
        raise ValueError('自定义重复规则缺少具体星期。')
    if repeat_type == 'ONCE' and not run_date_text:
        raise ValueError('单次策略缺少执行日期。')

    office_id = get_selected_office_id()

    if scope == 'device_collection':
        try:
            target_id = int((payload or {}).get('office_id'))
        except Exception as exc:
            raise ValueError('待确认策略缺少有效的办公室 ID。') from exc
        office_name = str((payload or {}).get('office_name') or '').strip()
        if not office_name:
            raise ValueError('待确认策略缺少办公室名称。')
        schedule = Schedule(
            office_id=office_id,
            name=name,
            target_type='COLLECTION',
            target_id=target_id,
            action=action,
            time_of_day=time_of_day,
            repeat_type=repeat_type,
            repeat_days=repeat_days,
            run_date=run_date if repeat_type == 'ONCE' else None,
            enabled=True,
        )
        db.session.add(schedule)
        db.session.commit()
        return {
            'ok': True,
            'type': 'action_result',
            'intent': 'device_schedule',
            'source': 'schedule_create',
            'reply': f'已创建单次定时策略“{name}”，将在 {run_date.isoformat()} {time_text} 对{office_name}的{str((payload or {}).get("device_type_label") or "全部设备")}执行{ACTION_LABELS[action]}。',
            'result': {
                'schedule_id': int(schedule.id),
                'name': schedule.name,
                'target_type': schedule.target_type,
                'target_id': schedule.target_id,
                'target_name': office_name,
                'action': schedule.action,
                'time_of_day': time_text,
                'run_date': run_date.isoformat(),
                'repeat_type': schedule.repeat_type,
                'repeat_days': schedule.repeat_days,
                'enabled': bool(schedule.enabled),
                'scope': 'device_collection',
                'office_name': office_name,
                'device_type_label': str((payload or {}).get('device_type_label') or '全部设备'),
                'device_count': int((payload or {}).get('device_count') or 0),
            },
        }

    try:
        target_id = int((payload or {}).get('target_id'))
    except Exception as exc:
        raise ValueError('待确认策略缺少有效的目标 ID。') from exc

    if target_type not in {'DEVICE', 'GROUP'}:
        raise ValueError('待确认策略缺少有效的目标类型。')

    if target_type == 'DEVICE':
        device = Device.query.get(target_id)
        if not device or device.office_id != office_id:
            raise ValueError('设备不存在')
    else:
        group = DeviceGroup.query.get(target_id)
        if not group or group.office_id != office_id:
            raise ValueError('分组不存在')

    schedule = Schedule(
        office_id=office_id,
        name=name,
        target_type=target_type,
        target_id=target_id,
        action=action,
        time_of_day=time_of_day,
        repeat_type=repeat_type,
        repeat_days=repeat_days,
        run_date=run_date if repeat_type == 'ONCE' else None,
        enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()

    return {
        'ok': True,
        'type': 'action_result',
        'intent': 'device_schedule',
        'source': 'schedule_create',
        'reply': f'已创建单次定时策略“{name}”，将在 {run_date.isoformat()} {time_text} 对{target_name}执行{ACTION_LABELS[action]}。',
        'result': {
            'schedule_id': int(schedule.id),
            'name': schedule.name,
            'target_type': schedule.target_type,
            'target_id': schedule.target_id,
            'target_name': target_name,
            'action': schedule.action,
            'time_of_day': time_text,
            'run_date': run_date.isoformat(),
            'repeat_type': schedule.repeat_type,
            'repeat_days': schedule.repeat_days,
            'enabled': bool(schedule.enabled),
        },
    }
