"""AI 助手设备控制编排与执行服务。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from extensions import db
from models import Device, DeviceCommand, DeviceGroup, DeviceGroupMember, Office
from services.agent_query_service import get_office_display_name
from services.office_context import get_selected_office_id
from services.state_log import log_device_state_change

ACTION_LABELS = {
    'ON': '打开',
    'OFF': '关闭',
}
TARGET_LABELS = {
    'device': '设备',
    'group': '分组',
}
ACTION_KEYWORDS = {
    'ON': ['打开', '开启', '启动'],
    'OFF': ['关闭', '关掉', '关上', '停掉', '断开'],
}
FILLER_KEYWORDS = ['帮我', '帮忙', '麻烦', '请', '把', '将', '给我', '一下', '一下子', '一下吧']
GROUP_HINT_KEYWORDS = ['分组', '组']
DEVICE_HINT_KEYWORDS = ['设备', '电器']
COLLECTION_SCOPE_KEYWORDS = ['所有', '全部', '全都', '整个办公室', '整个办公区', '整间办公室']
CURRENT_OFFICE_KEYWORDS = ['当前办公室', '本办公室', '这个办公室', '当前办公区', '本办公区']
DEVICE_TYPE_ALIASES = {
    '照明': {'灯', '照明', '灯光'},
    '空调': {'空调'},
    '插座': {'插座'},
    '电脑': {'电脑'},
    '打印机': {'打印机', '打印'},
    '投影仪': {'投影仪', '投影'},
    '风扇': {'风扇'},
}


class ControlResolutionError(ValueError):
    """控制意图无法落到唯一目标时抛出。"""


def is_probably_device_control(message: str) -> bool:
    text = (message or '').strip()
    if not text:
        return False
    if any(keyword in text for keyword in ['开会', '会议', '预约']):
        return False
    return any(keyword in text for words in ACTION_KEYWORDS.values() for keyword in words)


def _compact_text(text: str | None) -> str:
    raw = str(text or '').strip().lower()
    return re.sub(r'[\s\-_.·，。！？、,.:：;；“”"\'（）()\[\]{}]+', '', raw)


def _extract_action(message: str) -> str:
    text = (message or '').strip()
    for action, keywords in ACTION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return action
    raise ControlResolutionError('我只支持开关控制，请明确说“打开”或“关闭”某个设备或分组。')


def _extract_target_keyword(message: str) -> str:
    text = str(message or '')
    replacements = sorted(
        [item for words in ACTION_KEYWORDS.values() for item in words] + FILLER_KEYWORDS,
        key=len,
        reverse=True,
    )
    for token in replacements:
        text = text.replace(token, ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _score_match(keyword: str, *variants: str) -> int:
    target = _compact_text(keyword)
    if not target:
        return 0

    best = 0
    for variant in variants:
        name = _compact_text(variant)
        if not name:
            continue
        if target == name:
            best = max(best, 300 + len(name))
            continue
        if name in target:
            best = max(best, 220 + len(name))
        if target in name:
            best = max(best, 160 + len(target))
    return best


def _build_candidate_entry(target_type: str, row: Any, score: int) -> dict:
    if target_type == 'group':
        return {
            'target_type': 'group',
            'target_id': int(row.id),
            'target_name': row.name,
            'display_name': row.name,
            'location': '',
            'device_type': '',
            'is_on': bool(getattr(row, 'is_on', False)),
            'score': score,
            'priority': 2,
        }

    display_name = row.name
    if row.location:
        display_name = f'{row.location} · {row.name}'
    return {
        'target_type': 'device',
        'target_id': int(row.id),
        'target_name': row.name,
        'display_name': display_name,
        'location': row.location or '',
        'device_type': row.device_type or '其他',
        'is_on': bool(row.is_on),
        'score': score,
        'priority': 1,
    }


def _collect_candidates(message: str) -> list[dict]:
    office_id = get_selected_office_id()
    keyword = _extract_target_keyword(message)
    compact_message = _compact_text(message)
    compact_keyword = _compact_text(keyword)
    group_hint = any(token in message for token in GROUP_HINT_KEYWORDS)
    device_hint = any(token in message for token in DEVICE_HINT_KEYWORDS)

    candidates: list[dict] = []

    if not device_hint:
        groups = DeviceGroup.query.filter_by(office_id=office_id).order_by(DeviceGroup.id.asc()).all()
        for group in groups:
            score = max(
                _score_match(keyword, group.name),
                _score_match(compact_message, group.name),
                _score_match(compact_keyword, group.name),
            )
            if score > 0:
                candidates.append(_build_candidate_entry('group', group, score + (20 if group_hint else 0)))

    if not group_hint:
        devices = Device.query.filter_by(office_id=office_id).order_by(Device.id.asc()).all()
        for device in devices:
            score = max(
                _score_match(keyword, device.name, f'{device.location}{device.name}', f'{device.name}{device.location}'),
                _score_match(compact_message, device.name, f'{device.location}{device.name}', f'{device.name}{device.location}'),
                _score_match(compact_keyword, device.name, f'{device.location}{device.name}', f'{device.name}{device.location}'),
            )
            if score > 0:
                if device_hint:
                    score += 20
                candidates.append(_build_candidate_entry('device', device, score))

    candidates.sort(key=lambda item: (item['score'], item['priority'], item['target_id']), reverse=True)
    return candidates


def _build_candidate_text(candidate: dict) -> str:
    if candidate['target_type'] == 'group':
        return f"分组「{candidate['target_name']}」"
    meta = ' · '.join(part for part in [candidate.get('device_type'), candidate.get('location')] if part)
    return f"设备「{candidate['target_name']}」{('（' + meta + '）') if meta else ''}"


def _resolve_single_target(message: str) -> dict:
    candidates = _collect_candidates(message)
    if not candidates:
        raise ControlResolutionError('我没有在当前办公室找到对应的设备或分组，请换一个更准确的名称再试。')

    best = candidates[0]
    same_best = [item for item in candidates if item['score'] == best['score'] and item['priority'] == best['priority']]
    if len(same_best) > 1:
        preview = '、'.join(_build_candidate_text(item) for item in same_best[:3])
        raise ControlResolutionError(f'我匹配到了多个目标：{preview}。请说得更具体一些。')
    return best


def resolve_control_target(message: str, target_type_style: str = 'lower') -> dict:
    target = _resolve_single_target(message)
    if target_type_style == 'schedule':
        normalized_type = 'GROUP' if target['target_type'] == 'group' else 'DEVICE'
    else:
        normalized_type = target['target_type']
    return {
        **target,
        'target_type': normalized_type,
    }


def _is_collection_control(message: str) -> bool:
    text = str(message or '').strip()
    if not text:
        return False
    if any(keyword in text for keyword in COLLECTION_SCOPE_KEYWORDS):
        return True
    office = _match_office_from_message(text)
    if not office:
        return False
    if any(keyword in text for keyword in CURRENT_OFFICE_KEYWORDS):
        return True
    if '设备' not in text and '电器' not in text:
        return False
    return _extract_collection_device_type(text, _collect_office_devices(int(office.id))) is not None


def _match_office_from_message(message: str) -> Office | None:
    text = str(message or '').strip()
    if not text:
        return None
    offices = Office.query.order_by(Office.id.asc()).all()
    for office in offices:
        variants = {
            (office.name or '').strip(),
            (office.location or '').strip(),
            ((office.location or '').strip() + (office.name or '').strip()).strip(),
            (get_office_display_name(office) or '').replace(' · ', ''),
            (get_office_display_name(office) or '').strip(),
        }
        variants = {item for item in variants if item}
        if any(variant in text for variant in variants):
            return office
    return None


def _resolve_collection_office(message: str) -> Office | None:
    text = str(message or '').strip()
    if not text:
        return None
    office = _match_office_from_message(text)
    if office:
        return office
    if any(keyword in text for keyword in CURRENT_OFFICE_KEYWORDS):
        return Office.query.get(get_selected_office_id())
    return None


def _build_device_type_aliases(device_type: str | None) -> set[str]:
    keyword = str(device_type or '').strip()
    if not keyword:
        return set()
    aliases = {keyword}
    for canonical, items in DEVICE_TYPE_ALIASES.items():
        if keyword == canonical or keyword in items:
            aliases.update(items)
            aliases.add(canonical)
    return {item for item in aliases if item}


def _device_matches_type(device: Device, device_type: str | None) -> bool:
    aliases = _build_device_type_aliases(device_type)
    if not aliases:
        return True
    haystacks = [device.device_type or '', device.name or '', device.location or '']
    return any(alias in haystack for alias in aliases for haystack in haystacks)


def _extract_collection_device_type(message: str, devices: list[Device]) -> str | None:
    text = str(message or '').strip()
    if not text:
        return None

    for canonical, aliases in DEVICE_TYPE_ALIASES.items():
        if any(alias in text for alias in aliases | {canonical}):
            return canonical

    unique_types = []
    seen = set()
    for device in devices:
        dtype = str(device.device_type or '').strip()
        if dtype and dtype not in seen:
            seen.add(dtype)
            unique_types.append(dtype)
    unique_types.sort(key=len, reverse=True)

    for dtype in unique_types:
        if dtype in text:
            return dtype
    return None


def _collect_office_devices(office_id: int) -> list[Device]:
    return Device.query.filter_by(office_id=office_id).order_by(Device.id.asc()).all()


def _resolve_collection_target(message: str) -> dict | None:
    text = str(message or '').strip()
    if not _is_collection_control(text):
        return None

    office = _resolve_collection_office(text)
    if not office:
        raise ControlResolutionError('批量控制请先说明办公室名称，例如“打开默认办公室所有设备”。')

    office_name = get_office_display_name(office) or office.name or f'办公室 {office.id}'
    devices = _collect_office_devices(int(office.id))
    if not devices:
        raise ControlResolutionError(f'{office_name} 下暂无可控制的设备。')

    device_type = _extract_collection_device_type(text, devices)
    matched_devices = [device for device in devices if _device_matches_type(device, device_type)]
    if not matched_devices:
        if device_type:
            raise ControlResolutionError(f'{office_name} 下没有匹配“{device_type}”的设备。')
        raise ControlResolutionError(f'{office_name} 下暂无可控制的设备。')

    sample_devices = [device.name for device in matched_devices[:3]]
    device_type_label = f'{device_type}设备' if device_type else '全部设备'
    return {
        'scope': 'device_collection',
        'office_id': int(office.id),
        'office_name': office_name,
        'device_type': device_type,
        'device_type_label': device_type_label,
        'device_ids': [int(device.id) for device in matched_devices],
        'device_count': len(matched_devices),
        'sample_devices': sample_devices,
    }


def resolve_collection_target(message: str) -> dict | None:
    return _resolve_collection_target(message)


def build_pending_device_control(message: str) -> dict:
    action = _extract_action(message)
    action_label = ACTION_LABELS[action]

    collection_target = _resolve_collection_target(message)
    if collection_target is not None:
        summary = (
            f'即将{action_label}{collection_target["office_name"]}的'
            f'{collection_target["device_type_label"]}，共 {collection_target["device_count"]} 台'
        )
        details = [
            {'label': '操作', 'value': action_label},
            {'label': '办公室', 'value': collection_target['office_name']},
            {'label': '范围', 'value': collection_target['device_type_label']},
            {'label': '命中设备', 'value': f"{collection_target['device_count']} 台"},
        ]
        if collection_target['sample_devices']:
            details.append({'label': '示例设备', 'value': '、'.join(collection_target['sample_devices'])})
        pending_action = {
            'kind': 'device_control',
            'kind_label': '待确认设备控制',
            'scope': 'device_collection',
            'action': action,
            'action_label': action_label,
            'office_id': collection_target['office_id'],
            'office_name': collection_target['office_name'],
            'device_type': collection_target['device_type'],
            'device_type_label': collection_target['device_type_label'],
            'device_ids': collection_target['device_ids'],
            'device_count': collection_target['device_count'],
            'sample_devices': collection_target['sample_devices'],
            'summary': summary,
            'details': details,
        }
        return {
            'ok': True,
            'type': 'pending_action',
            'intent': 'device_control',
            'source': 'control_rule',
            'reply': (
                f'我已识别到你的批量控制指令，准备为你{action_label}'
                f'{collection_target["office_name"]}的{collection_target["device_type_label"]}。请确认执行。'
            ),
            'pendingAction': pending_action,
        }

    target = resolve_control_target(message)
    target_label = TARGET_LABELS[target['target_type']]
    current_state = '开启' if target['is_on'] else '关闭'
    pending_action = {
        'kind': 'device_control',
        'kind_label': '待确认设备控制',
        'action': action,
        'action_label': action_label,
        'target_type': target['target_type'],
        'target_type_label': target_label,
        'target_id': target['target_id'],
        'target_name': target['target_name'],
        'summary': f'即将{action_label}{target_label}“{target["target_name"]}”',
        'details': [
            {'label': '操作', 'value': action_label},
            {'label': '目标类型', 'value': target_label},
            {'label': '目标名称', 'value': target['target_name']},
            {'label': '当前状态', 'value': current_state},
        ],
    }
    return {
        'ok': True,
        'type': 'pending_action',
        'intent': 'device_control',
        'source': 'control_rule',
        'reply': f'我已识别到你的控制指令，准备为你{action_label}{target_label}“{target["target_name"]}”。请确认执行。',
        'pendingAction': pending_action,
    }


def _create_command_record(target_type: str, target_id: int, action: str) -> DeviceCommand:
    command = DeviceCommand(
        target_type=target_type.upper(),
        target_id=target_id,
        action=action,
        requested_by='ai_agent',
        result='PENDING',
    )
    db.session.add(command)
    db.session.commit()
    return command


def _mark_command_result(command_id: int, result: str, error_message: str | None = None) -> None:
    command = DeviceCommand.query.get(command_id)
    if not command:
        return
    command.result = result
    command.error_message = (error_message or '')[:255] or None
    command.executed_at = datetime.now()
    db.session.commit()


def _set_group_state(group_id: int, is_on: bool, office_id: int) -> dict:
    group = DeviceGroup.query.get(group_id)
    if not group or group.office_id != office_id:
        raise ControlResolutionError('对应分组不存在，可能已被删除或不在当前办公室。')

    device_ids = [
        row[0]
        for row in db.session.query(DeviceGroupMember.device_id)
        .filter(DeviceGroupMember.group_id == group_id)
        .all()
    ]
    devices = Device.query.filter(Device.id.in_(device_ids), Device.office_id == office_id).all() if device_ids else []

    changed = 0
    for device in devices:
        if log_device_state_change(device, is_on, source='agent'):
            changed += 1
            device.is_on = is_on

    return {
        'target_type': 'group',
        'target_id': int(group.id),
        'target_name': group.name,
        'affected': len(devices),
        'changed': changed,
        'is_on': bool(is_on),
    }


def _set_device_state(device_id: int, is_on: bool, office_id: int) -> dict:
    device = Device.query.get(device_id)
    if not device or device.office_id != office_id:
        raise ControlResolutionError('对应设备不存在，可能已被删除或不在当前办公室。')

    changed = 1 if log_device_state_change(device, is_on, source='agent') else 0
    device.is_on = is_on
    return {
        'target_type': 'device',
        'target_id': int(device.id),
        'target_name': device.name,
        'affected': 1,
        'changed': changed,
        'is_on': bool(is_on),
    }


def _set_collection_state(office_id: int, is_on: bool, device_type: str | None) -> dict:
    devices = _collect_office_devices(office_id)
    matched_devices = [device for device in devices if _device_matches_type(device, device_type)]
    office = Office.query.get(office_id)
    office_name = get_office_display_name(office) if office else f'办公室 {office_id}'

    if not matched_devices:
        if device_type:
            raise ControlResolutionError(f'{office_name} 下没有匹配“{device_type}”的设备。')
        raise ControlResolutionError(f'{office_name} 下暂无可控制的设备。')

    changed = 0
    for device in matched_devices:
        if log_device_state_change(device, is_on, source='agent'):
            changed += 1
            device.is_on = is_on

    return {
        'target_type': 'collection',
        'scope': 'device_collection',
        'office_id': office_id,
        'office_name': office_name,
        'device_type': device_type,
        'device_type_label': f'{device_type}设备' if device_type else '全部设备',
        'affected': len(matched_devices),
        'device_count': len(matched_devices),
        'changed': changed,
        'is_on': bool(is_on),
        'devices': [
            {
                'id': int(device.id),
                'name': device.name,
                'device_type': device.device_type or '其他',
                'location': device.location or '',
                'office_name': office_name,
                'is_on': bool(device.is_on),
            }
            for device in matched_devices[:8]
        ],
    }


def confirm_pending_device_control(payload: dict[str, Any]) -> dict:
    action = str((payload or {}).get('action') or '').strip().upper()
    scope = str((payload or {}).get('scope') or '').strip().lower()

    if action not in ACTION_LABELS:
        raise ValueError('待确认操作缺少有效的控制动作。')

    is_on = action == 'ON'
    action_label = ACTION_LABELS[action]

    if scope == 'device_collection':
        try:
            collection_office_id = int((payload or {}).get('office_id'))
        except Exception as exc:
            raise ValueError('待确认操作缺少有效的办公室 ID。') from exc
        device_type = str((payload or {}).get('device_type') or '').strip() or None
        office_name = str((payload or {}).get('office_name') or '').strip() or f'办公室 {collection_office_id}'
        command = _create_command_record('COLLECTION', collection_office_id, action)
        try:
            result = _set_collection_state(collection_office_id, is_on, device_type)
            _mark_command_result(int(command.id), 'SUCCESS')
        except Exception as exc:
            db.session.rollback()
            _mark_command_result(int(command.id), 'FAILED', str(exc))
            if isinstance(exc, ControlResolutionError):
                raise
            raise ValueError('设备控制执行失败，请稍后重试。') from exc

        changed = int(result.get('changed') or 0)
        affected = int(result.get('affected') or 0)
        scope_label = result.get('device_type_label') or '全部设备'
        if changed > 0:
            reply = f'已为你{action_label}{office_name}的{scope_label}，共影响 {affected} 台，其中状态实际变化 {changed} 台。'
        else:
            reply = f'{office_name}的{scope_label}已经全部处于{("开启" if is_on else "关闭")}状态，无需重复执行。'

        db.session.commit()
        return {
            'ok': True,
            'type': 'action_result',
            'intent': 'device_control',
            'source': 'control_execute',
            'reply': reply,
            'result': {
                **result,
                'command_id': int(command.id),
            },
        }

    target_type = str((payload or {}).get('target_type') or '').strip().lower()
    target_name = str((payload or {}).get('target_name') or '').strip()
    try:
        target_id = int((payload or {}).get('target_id'))
    except Exception as exc:
        raise ValueError('待确认操作缺少有效的目标 ID。') from exc

    if target_type not in TARGET_LABELS:
        raise ValueError('待确认操作缺少有效的目标类型。')

    office_id = get_selected_office_id()
    command = _create_command_record(target_type, target_id, action)
    try:
        if target_type == 'group':
            result = _set_group_state(target_id, is_on, office_id)
        else:
            result = _set_device_state(target_id, is_on, office_id)
        _mark_command_result(int(command.id), 'SUCCESS')
    except Exception as exc:
        db.session.rollback()
        _mark_command_result(int(command.id), 'FAILED', str(exc))
        if isinstance(exc, ControlResolutionError):
            raise
        raise ValueError('设备控制执行失败，请稍后重试。') from exc

    target_label = TARGET_LABELS[target_type]
    target_title = target_name or result.get('target_name') or ''
    changed = int(result.get('changed') or 0)
    affected = int(result.get('affected') or 0)
    if changed > 0:
        reply = f'已为你{action_label}{target_label}“{target_title}”，共影响 {affected} 个对象。'
    else:
        reply = f'{target_label}“{target_title}”已经是{("开启" if is_on else "关闭")}状态，无需重复执行。'

    db.session.commit()
    return {
        'ok': True,
        'type': 'action_result',
        'intent': 'device_control',
        'source': 'control_execute',
        'reply': reply,
        'result': {
            **result,
            'command_id': int(command.id),
        },
    }
