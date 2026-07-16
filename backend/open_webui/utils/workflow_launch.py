from __future__ import annotations

import copy
import mimetypes
from typing import Any


LAUNCH_MODES = {'instant', 'text_input', 'form_input', 'file_input'}
FOLLOW_UP_MODES = {'chat_about_result', 'rerun_each_message'}
CONFIRMATION_MODES = {'never', 'risk_only', 'always'}
FIELD_TYPES = {'string', 'integer', 'number', 'boolean'}
RISKY_NODE_TYPES = {
    'notification',
    'handoff',
    'http_request',
    'mcp_tools',
    'ticket_tool',
    'tool_call',
    'crm_tool',
    'code_interpreter',
}


def _node_type(node: dict[str, Any]) -> str:
    data = node.get('data') if isinstance(node.get('data'), dict) else {}
    return str(data.get('type') or data.get('kind') or node.get('type') or '').strip()


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def workflow_node_types(graph: dict[str, Any] | None) -> set[str]:
    nodes = graph.get('nodes') if isinstance(graph, dict) else []
    return {_node_type(node) for node in nodes or [] if isinstance(node, dict)}


def workflow_configured_model_ids(graph: dict[str, Any] | None) -> list[str]:
    nodes = graph.get('nodes') if isinstance(graph, dict) else []
    result: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        data = node.get('data') if isinstance(node.get('data'), dict) else {}
        config = data.get('config') if isinstance(data.get('config'), dict) else {}
        model_id = str(config.get('model_id') or '').strip()
        if model_id and model_id not in result:
            result.append(model_id)
    return result


def infer_launch_mode(graph: dict[str, Any] | None) -> str:
    node_types = workflow_node_types(graph)
    if 'file_upload' in node_types:
        return 'file_input'
    if 'form_input' in node_types:
        return 'form_input'
    if node_types.intersection({'schedule_trigger', 'webhook_trigger'}):
        return 'instant'
    return 'text_input'


def _default_schema(mode: str) -> dict[str, Any]:
    if mode == 'text_input':
        return {
            'type': 'object',
            'properties': {
                'message': {
                    'type': 'string',
                    'title': '訊息',
                    'description': '輸入要交給工作流處理的內容。',
                    'minLength': 1,
                }
            },
            'required': ['message'],
            'additionalProperties': False,
        }
    return {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}


def normalize_launch_contract(meta: dict[str, Any] | None, graph: dict[str, Any] | None) -> dict[str, Any]:
    source_meta = meta if isinstance(meta, dict) else {}
    source = source_meta.get('launch') if isinstance(source_meta.get('launch'), dict) else {}
    inferred_mode = infer_launch_mode(graph)
    mode = str(source.get('mode') or inferred_mode).strip()
    if mode not in LAUNCH_MODES:
        mode = inferred_mode

    schema = copy.deepcopy(source.get('inputSchema')) if isinstance(source.get('inputSchema'), dict) else None
    if schema is None:
        schema = _default_schema(mode)
    schema.setdefault('type', 'object')
    if not isinstance(schema.get('properties'), dict):
        schema['properties'] = {}
    if not isinstance(schema.get('required'), list):
        schema['required'] = []
    schema['required'] = [str(item).strip() for item in schema['required'] if str(item).strip()]
    schema.setdefault('additionalProperties', False)

    defaults = copy.deepcopy(source.get('defaultInput')) if isinstance(source.get('defaultInput'), dict) else {}
    follow_up_mode = str(source.get('followUpMode') or 'chat_about_result').strip()
    if follow_up_mode not in FOLLOW_UP_MODES:
        follow_up_mode = 'chat_about_result'
    confirmation = str(source.get('confirmation') or 'risk_only').strip()
    if confirmation not in CONFIRMATION_MODES:
        confirmation = 'risk_only'

    file_rules = source.get('fileRules') if isinstance(source.get('fileRules'), dict) else {}
    allowed_mime_types = file_rules.get('allowedMimeTypes')
    if not isinstance(allowed_mime_types, list):
        allowed_mime_types = ['image/*', 'audio/*', 'video/*', 'application/pdf', 'text/*']

    labels = {
        'instant': '立即執行',
        'text_input': '輸入問題',
        'form_input': '填寫條件',
        'file_input': '上傳檔案',
    }
    instructions = {
        'instant': '這個工作流已有執行所需資料，點擊後會直接開始。',
        'text_input': '輸入要交給工作流處理的內容。',
        'form_input': '填寫必要條件後開始執行。',
        'file_input': '上傳符合要求的檔案，可另外輸入處理要求。',
    }
    return {
        'version': 1,
        'mode': mode,
        'buttonLabel': str(source.get('buttonLabel') or labels[mode]).strip()[:40] or labels[mode],
        'instruction': str(source.get('instruction') or instructions[mode]).strip()[:500],
        'followUpMode': follow_up_mode,
        'confirmation': confirmation,
        'inputSchema': schema,
        'defaultInput': defaults,
        'fileRules': {
            'allowedMimeTypes': [str(item).strip() for item in allowed_mime_types if str(item).strip()][:30],
            'maxFiles': _bounded_int(file_rules.get('maxFiles'), 5, 1, 20),
            'maxSizeMB': _bounded_int(file_rules.get('maxSizeMB'), 25, 1, 500),
        },
    }


def normalize_launch_meta(
    meta: dict[str, Any] | None,
    graph: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = dict(meta) if isinstance(meta, dict) else {}
    normalized['launch'] = normalize_launch_contract(normalized, graph)
    return normalized


def workflow_has_risky_nodes(graph: dict[str, Any] | None) -> bool:
    return bool(workflow_node_types(graph).intersection(RISKY_NODE_TYPES))


def workflow_requires_confirmation(contract: dict[str, Any], graph: dict[str, Any] | None) -> bool:
    confirmation = contract.get('confirmation')
    return confirmation == 'always' or (confirmation == 'risk_only' and workflow_has_risky_nodes(graph))


def _field_value(payload: dict[str, Any], key: str) -> Any:
    if key == 'message':
        return payload.get('message')
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    return data.get(key)


def _is_missing(value: Any) -> bool:
    return value is None or value == '' or (isinstance(value, (list, dict)) and not value)


def _mime_allowed(mime_type: str, allowed: list[str]) -> bool:
    mime_type = mime_type.lower().strip()
    for pattern in allowed:
        pattern = pattern.lower().strip()
        if pattern == '*/*' or pattern == mime_type:
            return True
        if pattern.endswith('/*') and mime_type.startswith(pattern[:-1]):
            return True
    return False


def _file_mime(file_item: dict[str, Any]) -> str:
    explicit = str(file_item.get('content_type') or file_item.get('mimeType') or '').strip()
    if explicit:
        return explicit
    filename = str(file_item.get('name') or file_item.get('filename') or '')
    return mimetypes.guess_type(filename)[0] or 'application/octet-stream'


def apply_launch_defaults(contract: dict[str, Any], payload: dict[str, Any] | None) -> dict[str, Any]:
    source = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    defaults = contract.get('defaultInput') if isinstance(contract.get('defaultInput'), dict) else {}
    data = {key: value for key, value in defaults.items() if key != 'message'}
    supplied_data = source.get('data') if isinstance(source.get('data'), dict) else {}
    data.update(supplied_data)
    source['data'] = data
    if not source.get('message') and isinstance(defaults.get('message'), str):
        source['message'] = defaults['message']
    source.setdefault('message', '')
    source.setdefault('files', [])
    source.setdefault('parts', [])
    source.setdefault('context', {})
    return source


def validate_launch_input(
    contract: dict[str, Any],
    graph: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    normalized_payload = apply_launch_defaults(contract, payload)
    errors: list[str] = []
    missing_fields: list[str] = []
    schema = contract.get('inputSchema') if isinstance(contract.get('inputSchema'), dict) else {}
    properties = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}

    for key in schema.get('required') or []:
        if _is_missing(_field_value(normalized_payload, str(key))):
            missing_fields.append(str(key))

    for key, field in properties.items():
        if not isinstance(field, dict):
            continue
        value = _field_value(normalized_payload, key)
        if _is_missing(value):
            continue
        expected = field.get('type')
        valid_type = (
            expected == 'string'
            and isinstance(value, str)
            or expected == 'integer'
            and isinstance(value, int)
            and not isinstance(value, bool)
            or expected == 'number'
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            or expected == 'boolean'
            and isinstance(value, bool)
        )
        if expected in FIELD_TYPES and not valid_type:
            errors.append(f'{key} 的資料類型必須是 {expected}。')
            continue
        if isinstance(value, str):
            if field.get('minLength') is not None and len(value) < _bounded_int(field['minLength'], 0, 0, 100000):
                errors.append(f'{key} 長度不足。')
            if field.get('maxLength') is not None and len(value) > _bounded_int(field['maxLength'], 100000, 0, 100000):
                errors.append(f'{key} 長度超過限制。')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = field.get('minimum')
            maximum = field.get('maximum')
            if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
                errors.append(f'{key} 不得小於 {minimum}。')
            if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
                errors.append(f'{key} 不得大於 {maximum}。')
        enum = field.get('enum')
        if isinstance(enum, list) and enum and value not in enum:
            errors.append(f'{key} 不是允許的選項。')

    files = normalized_payload.get('files') if isinstance(normalized_payload.get('files'), list) else []
    if contract.get('mode') == 'file_input' and not files:
        missing_fields.append('files')
    if files:
        rules = contract.get('fileRules') if isinstance(contract.get('fileRules'), dict) else {}
        max_files = int(rules.get('maxFiles') or 5)
        max_size = int(rules.get('maxSizeMB') or 25) * 1024 * 1024
        allowed_mime_types = rules.get('allowedMimeTypes') or ['*/*']
        if len(files) > max_files:
            errors.append(f'檔案數量不得超過 {max_files} 個。')
        for file_item in files:
            if not isinstance(file_item, dict):
                errors.append('檔案資料格式不正確。')
                continue
            try:
                file_size = int(file_item.get('size') or 0)
            except (TypeError, ValueError):
                file_size = -1
            if file_size < 0:
                errors.append(f'{file_item.get("name") or "檔案"} 的檔案大小格式不正確。')
            elif file_size > max_size:
                errors.append(f'{file_item.get("name") or "檔案"} 超過 {rules.get("maxSizeMB")} MB。')
            mime_type = _file_mime(file_item)
            if not _mime_allowed(mime_type, allowed_mime_types):
                errors.append(f'{file_item.get("name") or "檔案"} 的格式 {mime_type} 不受支援。')

    missing_fields = list(dict.fromkeys(missing_fields))
    if missing_fields:
        errors.insert(0, '缺少必要輸入：' + '、'.join(missing_fields) + '。')
    requires_confirmation = workflow_requires_confirmation(contract, graph)
    if requires_confirmation and not confirmed:
        errors.append('這個工作流包含可能改變外部資料或發送內容的步驟，需要先確認。')

    return {
        'ok': not errors,
        'errors': errors,
        'missing_fields': missing_fields,
        'requires_confirmation': requires_confirmation,
        'input': normalized_payload,
    }


def validate_launch_contract(
    meta: dict[str, Any] | None,
    graph: dict[str, Any] | None,
    *,
    for_publish: bool = False,
) -> dict[str, list[str] | bool]:
    errors: list[str] = []
    warnings: list[str] = []
    raw = meta.get('launch') if isinstance(meta, dict) and isinstance(meta.get('launch'), dict) else None
    contract = normalize_launch_contract(meta, graph)
    if raw:
        if raw.get('mode') not in LAUNCH_MODES:
            errors.append('啟動模式無效。')
        if raw.get('followUpMode', 'chat_about_result') not in FOLLOW_UP_MODES:
            errors.append('後續對話模式無效。')
        if raw.get('confirmation', 'risk_only') not in CONFIRMATION_MODES:
            errors.append('執行確認模式無效。')

    schema = contract['inputSchema']
    if schema.get('type') != 'object':
        errors.append('輸入 Schema 的根節點必須是 object。')
    if schema.get('additionalProperties') is not False:
        errors.append('輸入 Schema 不允許未定義欄位。')
    properties = schema.get('properties') or {}
    for key, field in properties.items():
        if not str(key).strip():
            errors.append('輸入欄位必須有欄位代碼。')
            continue
        if not isinstance(field, dict) or field.get('type') not in FIELD_TYPES:
            errors.append(f'輸入欄位 {key} 使用了不支援的資料類型。')
            continue
        for constraint in ('minimum', 'maximum'):
            constraint_value = field.get(constraint)
            if constraint_value is not None and (
                not isinstance(constraint_value, (int, float)) or isinstance(constraint_value, bool)
            ):
                errors.append(f'輸入欄位 {key} 的 {constraint} 必須是數字。')
    unknown_required = [key for key in schema.get('required') or [] if key not in properties]
    if unknown_required:
        errors.append('必要欄位未定義：' + '、'.join(unknown_required) + '。')

    if contract['mode'] == 'instant':
        check = validate_launch_input(contract, graph, {'data': contract['defaultInput']}, confirmed=True)
        if check['missing_fields']:
            errors.append('立即執行模式必須為所有必要欄位提供預設值。')
    if contract['mode'] == 'form_input' and not properties:
        errors.append('填寫條件模式至少需要一個輸入欄位。')
    if contract['mode'] == 'file_input' and 'file_upload' not in workflow_node_types(graph):
        warnings.append('檔案輸入模式建議加入「檔案／多媒體輸入」節點。')
    if contract['confirmation'] == 'never' and workflow_has_risky_nodes(graph):
        message = '工作流包含外部動作節點，執行確認不得設為「不確認」。'
        (errors if for_publish else warnings).append(message)
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}
