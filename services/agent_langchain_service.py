"""LangChain 工具调用版 AI 助手服务。"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from flask import current_app

from services.agent_query_service import (
    find_unclosed_devices,
    get_device_energy_report,
    get_office_energy_report,
    normalize_date_range,
    resolve_office_by_name,
    summarize_top_office_in_range,
)

LANGCHAIN_IMPORT_ERROR: Exception | None = None

try:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - 依赖缺失时走兜底
    LANGCHAIN_IMPORT_ERROR = exc


UNSUPPORTED_CONTROL_REPLY = '我可以先帮你识别设备控制目标，并在你确认后执行开关操作。'
UNSUPPORTED_MEETING_REPLY = '当前仅开放查询和设备控制能力，会议预约将在下一阶段接入。'
UNKNOWN_REPLY = '我暂时没准确理解你的问题。你可以直接问：最近30天哪个办公室用电最多、默认办公室最近7天用电情况、还有哪些设备没关，或者说“关闭前台空调”。'


if LANGCHAIN_IMPORT_ERROR is None:
    class UnclosedDevicesInput(BaseModel):
        office_name: str | None = Field(default=None, description='办公室名称，可为空，例如 默认办公室、A区一号办公室。')
        device_type: str | None = Field(default=None, description='设备类型，可为空，例如 照明、空调。')


    class DateRangeInput(BaseModel):
        start: str | None = Field(default=None, description='开始日期，格式 YYYY-MM-DD；未知可留空。')
        end: str | None = Field(default=None, description='结束日期，格式 YYYY-MM-DD；未知可留空。')


    class OfficeDateRangeInput(DateRangeInput):
        office_name: str = Field(description='办公室名称，例如 默认办公室。')


    class DeviceDateRangeInput(DateRangeInput):
        office_name: str | None = Field(default=None, description='办公室名称，可为空；为空时表示全部办公室。')


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


    def _normalize_tool_dates(start: str | None, end: str | None) -> tuple[date, date]:
        normalized_start = _parse_iso_date(start)
        normalized_end = _parse_iso_date(end)
        default_days = int(current_app.config.get('AGENT_QUERY_DEFAULT_RANGE_DAYS', 30) or 30)
        return normalize_date_range(normalized_start, normalized_end, default_days=default_days)


    @tool('find_unclosed_devices_tool', args_schema=UnclosedDevicesInput)
    def find_unclosed_devices_tool(office_name: str | None = None, device_type: str | None = None) -> dict:
        """查询还未关闭的设备，可按办公室或设备类型筛选。"""
        return {
            'intent': 'check_unclosed_devices',
            'result': find_unclosed_devices(office_name=office_name, device_type=device_type),
        }


    @tool('compare_offices_energy_tool', args_schema=DateRangeInput)
    def compare_offices_energy_tool(start: str | None = None, end: str | None = None) -> dict:
        """查询指定时间范围内哪个办公室用电最多。"""
        normalized_start, normalized_end = _normalize_tool_dates(start, end)
        return {
            'intent': 'compare_offices_energy',
            'result': summarize_top_office_in_range(normalized_start, normalized_end),
        }


    @tool('office_energy_report_tool', args_schema=OfficeDateRangeInput)
    def office_energy_report_tool(office_name: str, start: str | None = None, end: str | None = None) -> dict:
        """查询某个办公室在指定时间范围内的用电报表。"""
        office = resolve_office_by_name(office_name)
        if not office:
            return {
                'intent': 'office_energy_report',
                'error': '没有找到对应的办公室，请检查名称后再试。',
            }
        normalized_start, normalized_end = _normalize_tool_dates(start, end)
        return {
            'intent': 'office_energy_report',
            'result': get_office_energy_report(int(office.id), normalized_start, normalized_end),
        }


    @tool('device_energy_report_tool', args_schema=DeviceDateRangeInput)
    def device_energy_report_tool(office_name: str | None = None, start: str | None = None, end: str | None = None) -> dict:
        """查询指定时间范围内的设备级用电报表，可限定办公室。"""
        office = resolve_office_by_name(office_name) if office_name else None
        if office_name and not office:
            return {
                'intent': 'device_energy_report',
                'error': '没有找到对应的办公室，请检查名称后再试。',
            }
        normalized_start, normalized_end = _normalize_tool_dates(start, end)
        return {
            'intent': 'device_energy_report',
            'result': get_device_energy_report(
                normalized_start,
                normalized_end,
                office_id=int(office.id) if office else None,
            ),
        }


    TOOLS = [
        find_unclosed_devices_tool,
        compare_offices_energy_tool,
        office_energy_report_tool,
        device_energy_report_tool,
    ]
    TOOLS_BY_NAME = {item.name: item for item in TOOLS}
else:  # pragma: no cover - 依赖缺失时走兜底
    TOOLS = []
    TOOLS_BY_NAME = {}


SYSTEM_PROMPT = """
你是办公室智能用电系统的 AI 助手。

你的工作规则：
1. 优先理解用户问题，并在需要真实数据时调用工具。
2. 如果用户是在打招呼、询问你是谁、你能做什么、如何使用，你可以直接自然回答，不需要调用工具。
3. 你可以回答查询问题，也可以识别设备控制意图。
4. 如果用户要求设备控制，不要直接声称已经执行；应明确告知会先生成待确认操作，再由系统执行。
5. 如果用户要求会议预约，明确回复：当前仅开放查询和设备控制能力，会议预约将在下一阶段接入。
6. 当用户提到最近7天、最近30天、本月、今天、昨天等时间范围时，可以自行换算成合适的 start/end 再调用工具。
7. 当用户没有明确给出时间范围但问题需要统计数据时，默认使用系统配置的查询时间范围。
8. 工具返回后，用自然中文总结结果，不要编造工具没有返回的数据。
9. 如果用户的表达不够明确，先结合上下文尽量理解；确实无法判断时，再请对方换一种更明确的说法。
""".strip()


def _create_llm() -> Any | None:
    if LANGCHAIN_IMPORT_ERROR is not None:
        return None
    api_key = (current_app.config.get('DEEPSEEK_API_KEY') or '').strip()
    if not api_key:
        return None
    base_url = (current_app.config.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/')
    model = (current_app.config.get('DEEPSEEK_MODEL') or 'deepseek-chat').strip()
    timeout_seconds = float(current_app.config.get('DEEPSEEK_TIMEOUT_SECONDS', 20) or 20)
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
        temperature=0,
    )


def _normalize_ai_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text') or item.get('content') or ''
                if text:
                    parts.append(str(text))
        return ''.join(parts).strip()
    return str(content or '').strip()


def _fallback_reply_from_tool(tool_payload: dict) -> str:
    intent = tool_payload.get('intent')
    result = tool_payload.get('result') or {}
    if intent == 'check_unclosed_devices':
        summary = result.get('summary') or {}
        device_count = int(summary.get('device_count') or 0)
        if device_count == 0:
            return '当前没有发现未关闭的设备。'
        office_count = int(summary.get('office_count') or 0)
        return f'当前发现 {office_count} 个办公室还有设备未关闭，共 {device_count} 台。'
    if intent == 'compare_offices_energy':
        top_office = result.get('top_office') or {}
        if not top_office:
            return '所选时间范围内还没有可用的用电统计数据。'
        return (
            f"{result.get('start')} 到 {result.get('end')} 期间，"
            f"用电最多的是 {top_office.get('display_name') or top_office.get('office_name')}，"
            f"总用电 {top_office.get('energy_kwh', 0)} kWh。"
        )
    if intent == 'office_energy_report':
        summary = result.get('summary') or {}
        return (
            f"{result.get('office_name')} 在 {result.get('start')} 到 {result.get('end')} "
            f"总用电 {summary.get('total_kwh', 0)} kWh，估算电费 {summary.get('total_cost', 0)} 元。"
        )
    if intent == 'device_energy_report':
        top_device = result.get('top_device') or {}
        if not top_device:
            return '所选时间范围内没有找到设备级用电数据。'
        scope = result.get('office_name') or '全部办公室'
        return (
            f"{scope} 在 {result.get('start')} 到 {result.get('end')} 期间，"
            f"最耗电设备是 {top_device.get('name')}，总用电 {top_device.get('energy_kwh', 0)} kWh。"
        )
    return UNKNOWN_REPLY


def _invoke_tool(tool_name: str, args: dict[str, Any]) -> dict:
    target = TOOLS_BY_NAME.get(tool_name)
    if not target:
        return {'ok': False, 'error': f'未注册工具：{tool_name}'}
    try:
        result = target.invoke(args)
    except Exception as exc:  # pragma: no cover - 模型调用异常时走兜底
        return {'ok': False, 'error': str(exc)}
    if isinstance(result, dict):
        return result
    return {'ok': True, 'result': result}


def handle_chat_message_with_langchain(message: str, session_id: str | None = None) -> dict | None:
    del session_id
    llm = _create_llm()
    if llm is None:
        return None

    max_tool_calls = max(int(current_app.config.get('AGENT_LANGCHAIN_MAX_TOOL_CALLS', 4) or 4), 1)
    llm_with_tools = llm.bind_tools(TOOLS)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=message),
    ]
    last_tool_payload: dict | None = None

    try:
        for _ in range(max_tool_calls):
            ai_message = llm_with_tools.invoke(messages)
            messages.append(ai_message)
            tool_calls = getattr(ai_message, 'tool_calls', None) or []
            if not tool_calls:
                reply = _normalize_ai_text(ai_message.content) or (last_tool_payload and _fallback_reply_from_tool(last_tool_payload)) or UNKNOWN_REPLY
                if last_tool_payload and last_tool_payload.get('result') is not None:
                    return {
                        'ok': True,
                        'type': 'query_result',
                        'intent': last_tool_payload.get('intent') or 'unknown',
                        'reply': reply,
                        'result': last_tool_payload['result'],
                        'source': 'langchain',
                    }
                return {
                    'ok': True,
                    'type': 'text',
                    'intent': 'unknown',
                    'reply': reply,
                    'source': 'langchain',
                }

            for tool_call in tool_calls:
                tool_name = tool_call.get('name')
                args = tool_call.get('args') or {}
                tool_payload = _invoke_tool(tool_name, args)
                if isinstance(tool_payload, dict) and tool_payload.get('result') is not None:
                    last_tool_payload = tool_payload
                messages.append(
                    ToolMessage(
                        content=json.dumps(tool_payload, ensure_ascii=False),
                        tool_call_id=tool_call['id'],
                    )
                )
    except Exception:  # pragma: no cover - 网络或模型异常时走兜底
        current_app.logger.exception('LangChain agent call failed')
        return None

    if last_tool_payload and last_tool_payload.get('result') is not None:
        return {
            'ok': True,
            'type': 'query_result',
            'intent': last_tool_payload.get('intent') or 'unknown',
            'reply': _fallback_reply_from_tool(last_tool_payload),
            'result': last_tool_payload['result'],
            'source': 'langchain',
        }
    return {
        'ok': True,
        'type': 'text',
        'intent': 'unknown',
        'reply': UNKNOWN_REPLY,
        'source': 'langchain',
    }
