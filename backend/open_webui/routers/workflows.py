import hmac
import json
import logging
import os
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.chats import Chats
from open_webui.models.interact_data_connectors import InteractDataConnectors
from open_webui.models.interact_semantic import InteractSemantic
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.models.workflows import (
    WorkflowForm,
    WorkflowListResponse,
    WorkflowModel,
    WorkflowPatchForm,
    WorkflowRunForm,
    WorkflowRunModel,
    Workflows,
    WorkflowValidateResponse,
    WorkflowVersionModel,
)
from open_webui.semantic_query.contracts import QueryRuntimeContext
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.service import (
    _connector_allowed as semantic_connector_allowed,
    _dataset_allowed as semantic_dataset_allowed,
    execute_query as execute_semantic_query,
)
from open_webui.tools.builtin import query_knowledge_files
from open_webui.tools.interact_database import (
    QueryContext as DatabaseQueryContext,
    _connector_denial_reason,
    interact_database_query,
)
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from open_webui.utils.misc import get_message_list
from open_webui.utils.models import get_all_models, get_filtered_models
from open_webui.utils.workflow_runtime import (
    RUNTIME_MODEL_TYPES,
    WorkflowRuntimeError,
    execute_workflow_graph,
    node_semantic_type,
    runtime_unsupported_node_types,
    workflow_outputs_text,
    workflow_outputs_to_response_items,
)
from open_webui.utils.workflow_launch import (
    apply_launch_defaults,
    normalize_launch_contract,
    validate_launch_contract,
    validate_launch_input,
    workflow_configured_model_ids,
)
from open_webui.utils.workflows import (
    WorkflowAccessContext,
    decide_workflow_candidates,
    normalize_workflow_meta,
    validate_workflow_agent_policy,
    validate_workflow_graph,
    validate_workflow_visibility,
    workflow_acl_allows,
    workflow_agent_candidate,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
log = logging.getLogger(__name__)

PAGE_ITEM_COUNT = 30


def _service_token() -> str:
    return (
        os.environ.get('INTERACT_CHANNEL_SERVICE_TOKEN')
        or os.environ.get('INTERACT_BILLING_SERVICE_TOKEN')
        or os.environ.get('OPEN_WEBUI_BILLING_SERVICE_TOKEN')
        or ''
    ).strip()


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ''
    scheme, _, value = authorization.partition(' ')
    return value.strip() if scheme.lower() == 'bearer' else ''


def _require_service_token(authorization: str | None, x_interact_service_token: str | None) -> None:
    expected = _service_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Interact workflow service token is not configured.',
        )

    supplied = (x_interact_service_token or '').strip() or _bearer_token(authorization)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid service token.')


async def check_workflows_permission(user):
    config = await Config.get_many('workflows.enable', 'user.permissions')
    if config.get('workflows.enable') is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    permissions = config.get('user.permissions') or {}
    features = permissions.get('features') if isinstance(permissions, dict) else None
    if isinstance(features, dict) and 'workflows' in features:
        if user.role != 'admin' and not await has_permission(user.id, 'features.workflows', permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )


def check_workflow_access(
    workflow: Optional[WorkflowModel],
    user,
    context: WorkflowAccessContext,
    allow_public_template: bool = True,
):
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    if not workflow_acl_allows(workflow, context, allow_public_template=allow_public_template):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)


def check_workflow_owner(workflow: Optional[WorkflowModel], user):
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    if user.role != 'admin' and user.id != workflow.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)


class ServiceCompanyWorkflowRequest(WorkflowForm):
    companyEmail: str
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None
    channelId: Optional[str] = None
    modelId: Optional[str] = None


class ServiceCompanyRequest(WorkflowRunForm):
    companyEmail: str
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None
    channelId: Optional[str] = None
    modelId: Optional[str] = None


class ServiceCompanyListRequest(WorkflowRunForm):
    companyEmail: str
    query: Optional[str] = None
    visibility: Optional[str] = None
    page: Optional[int] = 1
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None
    channelId: Optional[str] = None
    modelId: Optional[str] = None


class WorkflowAgentSelectorRequest(BaseModel):
    message: str = ''
    channelId: Optional[str] = None
    modelId: Optional[str] = None
    maxItems: int = Field(default=5, ge=1, le=20)


class ServiceWorkflowAgentSelectorRequest(WorkflowAgentSelectorRequest):
    companyEmail: str
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None


class WorkflowAgentSelectorItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    visibility: str
    default_version_id: Optional[str] = None
    score: float
    confidence: float
    threshold: float
    priority: int = 0
    ambiguity_margin: float
    matched_keywords: list[str] = Field(default_factory=list)
    matched_required_keywords: list[str] = Field(default_factory=list)
    matched_examples: list[dict[str, Any]] = Field(default_factory=list)
    reason: str


class WorkflowAgentSelectorResponse(BaseModel):
    decision: Literal['selected', 'ambiguous', 'none']
    action: Literal['execute_workflow', 'ask_user', 'continue_chat']
    selected_workflow_id: Optional[str] = None
    selected_version_id: Optional[str] = None
    needs_confirmation: bool = False
    reason: str
    items: list[WorkflowAgentSelectorItem]


class WorkflowLaunchPreflightRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    workflow_version_id: Optional[str] = None
    model_id: Optional[str] = None
    channel_id: Optional[str] = None
    surface: Literal['webui_chat', 'channel', 'api'] = 'webui_chat'
    confirmed: bool = False


class WorkflowLaunchCheck(BaseModel):
    code: str
    status: Literal['pass', 'warning', 'fail']
    message: str


class WorkflowLaunchPreflightResponse(BaseModel):
    ok: bool
    workflow_id: str
    workflow_version_id: Optional[str] = None
    launch: dict[str, Any]
    effective_model_id: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    checks: list[WorkflowLaunchCheck] = Field(default_factory=list)


class ServiceWorkflowLaunchPreflightRequest(WorkflowLaunchPreflightRequest):
    companyEmail: str
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None
    channelId: Optional[str] = None
    modelId: Optional[str] = None


async def _resolve_service_user(company_email: str, db: AsyncSession):
    user = await Users.get_user_by_email(company_email.strip().lower(), db=db)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Company WebUI user not found.')
    return user


async def _workflow_context_for_user(
    user,
    db: AsyncSession | None,
    channel_id: str | None = None,
    model_id: str | None = None,
) -> WorkflowAccessContext:
    groups = await Groups.get_groups_by_member_id(user.id, db=db)
    context = WorkflowAccessContext(
        user_id=user.id,
        role=user.role,
        company_user_id=user.id,
        group_ids={group.id for group in groups},
        channel_id=channel_id,
        model_id=model_id,
    )

    if is_billing_enabled():
        try:
            identity = await InteractBillingClient().resolve_identity(user)
            company_user = identity.company_user or {}
            company_member = identity.company_member or {}
            context.company_user_id = company_user.get('id') or context.company_user_id
            context.company_member_id = company_member.get('id')
            context.company_member_role = company_member.get('role')
        except Exception:
            pass

    return context


def _workflow_context_for_service_user(user, form_data: Any) -> WorkflowAccessContext:
    return WorkflowAccessContext(
        user_id=user.id,
        role='user',
        company_user_id=(getattr(form_data, 'companyUserId', None) or user.id),
        company_member_id=getattr(form_data, 'companyMemberId', None),
        company_member_role=getattr(form_data, 'companyMemberRole', None),
        channel_id=getattr(form_data, 'channelId', None) or getattr(form_data, 'channel_id', None),
        model_id=getattr(form_data, 'modelId', None) or getattr(form_data, 'model_id', None),
    )


def _paginate_workflows(items: list[WorkflowModel], page: int, limit: int) -> WorkflowListResponse:
    start = (page - 1) * limit
    return WorkflowListResponse(items=items[start : start + limit], total=len(items))


def _validate_workflow_configuration(
    graph: dict[str, Any],
    visibility: str | None,
    meta: dict[str, Any] | None,
    for_publish: bool = False,
) -> dict[str, list[str] | bool]:
    graph_validation = validate_workflow_graph(graph)
    agent_validation = validate_workflow_agent_policy(meta, visibility)
    launch_validation = validate_launch_contract(meta, graph, for_publish=for_publish)
    errors = [
        *graph_validation['errors'],
        *validate_workflow_visibility(graph, visibility, meta),
        *agent_validation['errors'],
        *launch_validation['errors'],
    ]
    warnings = [
        *graph_validation['warnings'],
        *agent_validation['warnings'],
        *launch_validation['warnings'],
    ]
    acl = meta.get('acl') if isinstance(meta, dict) and isinstance(meta.get('acl'), dict) else {}
    launch = normalize_launch_contract(meta, graph)
    if acl.get('allow_agent_selection') and launch['mode'] in {'form_input', 'file_input'}:
        message = '需要表單或檔案的工作流不能由文字意圖選擇器自動執行，請關閉「允許代理自動選擇」。'
        (errors if for_publish else warnings).append(message)
    unsupported_nodes = runtime_unsupported_node_types(graph)
    if unsupported_nodes:
        message = 'Workflow runtime handlers are not available for: ' + ', '.join(unsupported_nodes) + '.'
        if for_publish:
            errors.append(message)
        else:
            warnings.append(message)
    return {'ok': len(errors) == 0, 'errors': errors, 'warnings': warnings}


async def _validate_semantic_nodes_for_publish(
    workflow: WorkflowModel,
    company_user_id: str | None,
) -> list[str]:
    errors: list[str] = []
    nodes = workflow.graph.get('nodes') if isinstance(workflow.graph, dict) else []
    for node in nodes or []:
        if not isinstance(node, dict) or node_semantic_type(node) != 'semantic_query':
            continue
        node_id = str(node.get('id') or 'unknown')
        data = node.get('data') if isinstance(node.get('data'), dict) else {}
        config = data.get('config') if isinstance(data.get('config'), dict) else {}
        plan = config.get('plan') if isinstance(config.get('plan'), dict) else {}
        dataset_id = str(config.get('dataset_id') or plan.get('datasetId') or '').strip()
        dataset = await InteractSemantic.get_dataset(dataset_id) if dataset_id else None
        if not dataset or dataset.get('company_user_id') != company_user_id:
            errors.append(f'Semantic query node {node_id} cannot access dataset {dataset_id or "(missing)"}.')
            continue
        if dataset.get('status') != 'published' or not dataset.get('current_version_id'):
            errors.append(f'Semantic query node {node_id} requires a published dataset version.')
        allowed_workflows = dataset.get('allowed_workflow_ids') or []
        if allowed_workflows and workflow.id not in allowed_workflows:
            errors.append(f'Semantic query node {node_id} dataset does not allow workflow {workflow.id}.')
        connector = await InteractDataConnectors.get_by_id(dataset['connector_id'])
        if not connector or not connector.enabled or connector.company_user_id != company_user_id:
            errors.append(f'Semantic query node {node_id} connector is disabled or belongs to another company.')
    return errors


def _launch_check(code: str, check_status: str, message: str) -> WorkflowLaunchCheck:
    return WorkflowLaunchCheck(code=code, status=check_status, message=message)


def _effective_workflow_model_id(graph: dict[str, Any], requested_model_id: str | None) -> str | None:
    configured = workflow_configured_model_ids(graph)
    if len(configured) == 1:
        return configured[0]
    if requested_model_id:
        return requested_model_id
    return configured[0] if configured else None


async def _workflow_dependency_preflight(
    request: Request,
    workflow: WorkflowModel,
    user,
    graph: dict[str, Any],
    context: WorkflowAccessContext,
    surface: str,
) -> list[WorkflowLaunchCheck]:
    checks: list[WorkflowLaunchCheck] = []
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    database_context = DatabaseQueryContext(
        user_id=context.user_id,
        user_role=context.role,
        model_id=context.model_id,
        channel_id=context.channel_id,
        channel_source='channel' if surface == 'channel' else surface,
        company_user_id=context.company_user_id,
        company_member_id=context.company_member_id,
        company_member_role=context.company_member_role,
        group_ids=list(context.group_ids),
    )
    semantic_context = QueryRuntimeContext(
        user_id=context.user_id,
        user_role=context.role,
        company_user_id=str(context.company_user_id or ''),
        company_member_id=context.company_member_id,
        company_member_role=context.company_member_role,
        group_ids=list(context.group_ids),
        model_id=context.model_id,
        channel_id=context.channel_id,
        channel_source='channel' if surface == 'channel' else surface,
        workflow_id=workflow.id,
    )

    seen_connectors: set[str] = set()
    seen_datasets: set[str] = set()
    seen_knowledges: set[str] = set()

    configured_models = workflow_configured_model_ids(graph)
    effective_model_id = _effective_workflow_model_id(graph, context.model_id)
    if configured_models or effective_model_id:
        try:
            cached_models = getattr(request.app.state, 'MODELS', None)
            try:
                all_models = list(cached_models.values()) if cached_models else []
            except (AttributeError, TypeError):
                all_models = []
            if not all_models:
                all_models = await get_all_models(request, user=user)
            accessible_models = await get_filtered_models(all_models, user)
            accessible_model_ids = {str(model.get('id')) for model in accessible_models}
            if not effective_model_id:
                checks.append(
                    _launch_check('model_required', 'fail', '此工作流需要模型，但尚未指定聊天模型或固定模型。')
                )
            elif effective_model_id not in accessible_model_ids:
                checks.append(
                    _launch_check(
                        'model_not_available',
                        'fail',
                        f'模型「{effective_model_id}」不存在、已停用或目前帳號沒有讀取權限。',
                    )
                )
            else:
                checks.append(
                    _launch_check('model_access', 'pass', f'模型「{effective_model_id}」可供目前帳號使用。')
                )
        except Exception as exc:
            log.warning('Workflow model preflight failed for %s: %s', workflow.id, exc)
            checks.append(
                _launch_check('model_check_failed', 'fail', '目前無法確認模型是否可用，請重新整理模型清單後再試。')
            )

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node_semantic_type(node)
        data = node.get('data') if isinstance(node.get('data'), dict) else {}
        config = data.get('config') if isinstance(data.get('config'), dict) else {}
        if node_type == 'knowledge_query':
            knowledge_ids = config.get('knowledge_ids')
            if not isinstance(knowledge_ids, list) or not knowledge_ids:
                checks.append(
                    _launch_check('knowledge_dynamic', 'pass', '知識庫節點會在執行時搜尋目前帳號可讀取的知識庫。')
                )
                continue
            for raw_knowledge_id in knowledge_ids:
                knowledge_id = str(raw_knowledge_id or '').strip()
                if not knowledge_id or knowledge_id in seen_knowledges:
                    continue
                seen_knowledges.add(knowledge_id)
                knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
                if not knowledge:
                    checks.append(_launch_check('knowledge_not_found', 'fail', f'找不到知識庫 {knowledge_id}。'))
                elif not await Knowledges.check_access_by_user_id(knowledge_id, user.id, permission='read'):
                    checks.append(
                        _launch_check(
                            'knowledge_access_denied',
                            'fail',
                            f'目前帳號沒有知識庫「{knowledge.name}」的讀取權限。',
                        )
                    )
                else:
                    checks.append(
                        _launch_check('knowledge_access', 'pass', f'知識庫「{knowledge.name}」允許目前帳號讀取。')
                    )
        elif node_type == 'semantic_query':
            plan = config.get('plan') if isinstance(config.get('plan'), dict) else {}
            dataset_id = str(config.get('dataset_id') or plan.get('datasetId') or '').strip()
            if not dataset_id or dataset_id in seen_datasets:
                continue
            seen_datasets.add(dataset_id)
            dataset = await InteractSemantic.get_dataset(dataset_id)
            if not dataset:
                checks.append(_launch_check('dataset_not_found', 'fail', f'找不到資料集 {dataset_id}。'))
                continue
            connector = await InteractDataConnectors.get_by_id(dataset.get('connector_id'))
            if not connector:
                checks.append(_launch_check('connector_not_found', 'fail', '資料集使用的連接器不存在。'))
                continue
            try:
                semantic_connector_allowed(connector, semantic_context)
                semantic_dataset_allowed(dataset, semantic_context)
            except SemanticQueryError as error:
                checks.append(_launch_check(error.code.lower().replace('_', '-'), 'fail', error.public()['message']))
                continue
            checks.append(_launch_check('semantic_access', 'pass', f'資料集「{dataset.get("name") or dataset_id}」已發布且允許本次執行。'))
        elif node_type == 'database_query':
            connector_id = str(config.get('connector_id') or '').strip()
            if not connector_id or connector_id == 'webui_local' or connector_id in seen_connectors:
                continue
            seen_connectors.add(connector_id)
            connector = await InteractDataConnectors.get_by_id(connector_id)
            if not connector:
                checks.append(_launch_check('connector_not_found', 'fail', f'找不到資料庫連接器 {connector_id}。'))
                continue
            reason = _connector_denial_reason(connector, database_context)
            if reason:
                checks.append(_launch_check('database_access_denied', 'fail', f'資料庫連接器不可用：{reason}。'))
                continue
            table = str(config.get('table') or '').strip()
            if table and connector.allowed_tables and table not in connector.allowed_tables:
                checks.append(_launch_check('table_not_allowed', 'fail', f'資料表 {table} 未列入連接器授權。'))
                continue
            checks.append(_launch_check('database_access', 'pass', f'資料庫連接器「{connector.name}」已啟用並允許本次執行。'))
    if not checks:
        checks.append(_launch_check('dependencies', 'pass', '工作流沒有需要額外預檢的資料來源。'))
    return checks


def _chat_message_content(message: dict[str, Any]) -> str:
    content = message.get('content')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return '\n'.join(
            str(part.get('text') or '').strip()
            for part in content
            if isinstance(part, dict) and part.get('type') == 'text' and part.get('text')
        ).strip()
    return ''


async def _workflow_chat_context(
    user,
    workflow_request: dict[str, Any],
    form_data: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not workflow_request.get('includeChatContext'):
        return {}

    history: list[dict[str, Any]] = []
    supplied_messages = form_data.get('messages')
    if isinstance(supplied_messages, list):
        history = [item for item in supplied_messages if isinstance(item, dict)]

    if not history and metadata.get('chat_id'):
        chat = await Chats.get_chat_by_id_and_user_id(str(metadata['chat_id']), user.id)
        if chat:
            stored_history = chat.chat.get('history') or {}
            messages_map = stored_history.get('messages') or {}
            current_id = metadata.get('user_message_id') or stored_history.get('currentId')
            history = get_message_list(messages_map, current_id)
            if not history and current_id != stored_history.get('currentId'):
                history = get_message_list(messages_map, stored_history.get('currentId'))

    current_message = metadata.get('user_message') if isinstance(metadata.get('user_message'), dict) else {}
    current_content = _chat_message_content(current_message)
    normalized: list[dict[str, str]] = []
    for message in history:
        role = str(message.get('role') or '').strip()
        content = _chat_message_content(message)
        if role not in {'user', 'assistant'} or not content:
            continue
        normalized.append({'role': role, 'content': content[:8000]})

    if normalized and normalized[-1]['role'] == 'user' and current_content:
        if normalized[-1]['content'] == current_content[:8000]:
            normalized.pop()
    normalized = normalized[-20:]
    return {
        'chat_history': normalized,
        'conversation': '\n'.join(f'{item["role"]}: {item["content"]}' for item in normalized)[:40000],
    }


def _response_data(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if isinstance(response, JSONResponse):
        try:
            decoded = json.loads(response.body.decode('utf-8', 'replace'))
            return decoded if isinstance(decoded, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
    return {}


def _response_text(response_data: dict[str, Any]) -> str:
    choices = response_data.get('choices') if isinstance(response_data.get('choices'), list) else []
    if choices:
        message = choices[0].get('message') if isinstance(choices[0], dict) else {}
        content = message.get('content') if isinstance(message, dict) else ''
        if isinstance(content, str):
            return content
    return ''


async def _execute_workflow(
    request: Request,
    user,
    workflow: WorkflowModel,
    form_data: WorkflowRunForm,
) -> dict[str, Any]:
    version_id = form_data.workflow_version_id
    use_draft = form_data.trigger_type in {'manual_test', 'test.editor'} and not version_id
    if not use_draft:
        version_id = version_id or workflow.default_version_id
        if not version_id:
            raise WorkflowRuntimeError('Publish this workflow before running it outside the editor.')
        version = await Workflows.get_version_by_id(version_id)
        if not version or version.workflow_id != workflow.id:
            raise WorkflowRuntimeError('Published workflow version was not found.')
        graph = version.graph
        version_meta = version.meta
    else:
        graph = workflow.graph
        version_meta = workflow.meta

    launch = normalize_launch_contract(version_meta, graph)
    launch_input = validate_launch_input(
        launch,
        graph,
        form_data.input,
        confirmed=form_data.confirmed or use_draft,
    )
    if not launch_input['ok']:
        raise WorkflowRuntimeError(' '.join(launch_input['errors']))
    form_data.input = launch_input['input']
    form_data.model_id = _effective_workflow_model_id(graph, form_data.model_id)

    async def model_runner(
        prompt: str,
        system_prompt: str | None,
        model_id: str | None,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resolved_model_id = model_id or form_data.model_id
        if not resolved_model_id:
            raise WorkflowRuntimeError(
                'This workflow requires a model. Select one in chat or configure the model node.'
            )
        content: str | list[dict[str, Any]] = prompt
        image_parts = [part for part in parts if part.get('type') == 'image' and part.get('url')]
        if image_parts:
            content = [
                {'type': 'text', 'text': prompt},
                *[{'type': 'image_url', 'image_url': {'url': part['url']}} for part in image_parts],
            ]
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': content})
        response = await generate_chat_completion(
            request,
            {'model': resolved_model_id, 'messages': messages, 'stream': False},
            user=user,
        )
        response_data = _response_data(response)
        if response_data.get('error'):
            raise WorkflowRuntimeError(str(response_data['error']))
        return {
            'text': _response_text(response_data),
            'usage': response_data.get('usage') or {},
            'model_id': response_data.get('model') or resolved_model_id,
        }

    async def node_runner(
        node_type: str,
        config: dict[str, Any],
        incoming: Any,
        workflow_input: dict[str, Any],
    ) -> Any:
        if node_type not in {'knowledge_query', 'database_query', 'semantic_query'}:
            raise WorkflowRuntimeError(f'No secure runtime is registered for {node_type}.')
        if node_type == 'knowledge_query':
            query = str(config.get('query') or '').strip()
            incoming_text = ''
            if isinstance(incoming, dict):
                incoming_text = str(incoming.get('text') or incoming.get('message') or '').strip()
                if not incoming_text:
                    incoming_text = json.dumps(incoming, ensure_ascii=False, default=str)
            elif incoming is not None:
                incoming_text = str(incoming).strip()
            query = query.replace('{{message}}', str(workflow_input.get('message') or ''))
            query = query.replace('{{input}}', incoming_text)
            if not query:
                query = incoming_text
            query = query or str(workflow_input.get('message') or '').strip()
            if not query:
                raise WorkflowRuntimeError('Knowledge query requires a search question.')

            knowledge_ids = config.get('knowledge_ids')
            if not isinstance(knowledge_ids, list):
                knowledge_ids = None
            raw_result = await query_knowledge_files(
                query=query,
                knowledge_ids=knowledge_ids,
                count=max(1, min(20, int(config.get('count') or 5))),
                __request__=request,
                __user__={'id': user.id, 'role': user.role},
            )
            try:
                result = json.loads(raw_result)
            except (TypeError, json.JSONDecodeError) as exc:
                raise WorkflowRuntimeError('Knowledge query returned an invalid response.') from exc
            if isinstance(result, dict) and result.get('error'):
                raise WorkflowRuntimeError(str(result['error']))
            return result

        access_context = await _workflow_context_for_user(
            user,
            None,
            channel_id=form_data.channel_id,
            model_id=form_data.model_id,
        )
        if node_type == 'semantic_query':
            configured_plan = config.get('plan') if isinstance(config.get('plan'), dict) else {}
            incoming_plan: dict[str, Any] = {}
            if config.get('use_incoming_plan'):
                if isinstance(incoming, dict):
                    incoming_plan = incoming.get('plan') if isinstance(incoming.get('plan'), dict) else incoming
                elif isinstance(incoming, str):
                    try:
                        decoded = json.loads(incoming)
                        incoming_plan = decoded if isinstance(decoded, dict) else {}
                    except json.JSONDecodeError:
                        incoming_plan = {}
            plan = {**configured_plan, **incoming_plan}
            if config.get('dataset_id'):
                plan['datasetId'] = str(config['dataset_id'])
            plan.setdefault('version', '1')
            try:
                return await execute_semantic_query(
                    plan,
                    QueryRuntimeContext(
                        user_id=user.id,
                        user_role=user.role,
                        company_user_id=str(access_context.company_user_id or ''),
                        company_member_id=access_context.company_member_id,
                        company_member_role=access_context.company_member_role,
                        group_ids=list(access_context.group_ids),
                        model_id=form_data.model_id,
                        channel_id=form_data.channel_id,
                        channel_source='channel' if form_data.channel_id else 'workflow',
                        workflow_id=workflow.id,
                    ),
                )
            except SemanticQueryError as error:
                raise WorkflowRuntimeError(f'{error.public()["message"]}（錯誤代碼：{error.code}）') from error
        raw_result = await interact_database_query(
            connector_id=str(config.get('connector_id') or 'webui_local'),
            table=str(config.get('table') or ''),
            operation=str(config.get('operation') or 'select'),
            columns=config.get('columns') if isinstance(config.get('columns'), list) else None,
            filters=config.get('filters') if isinstance(config.get('filters'), dict) else None,
            order_by=config.get('order_by') if isinstance(config.get('order_by'), list) else None,
            group_by=config.get('group_by') if isinstance(config.get('group_by'), list) else None,
            limit=max(1, min(1000, int(config.get('limit') or 20))),
            __request__=request,
            __user__={
                'id': user.id,
                'role': user.role,
                'company_user_id': access_context.company_user_id,
                'company_member_id': access_context.company_member_id,
                'company_member_role': access_context.company_member_role,
            },
            __metadata__={
                'model_id': form_data.model_id,
                'channel_id': form_data.channel_id,
                'source': 'channel' if form_data.channel_id else 'workflow',
                'group_ids': list(access_context.group_ids),
            },
        )
        try:
            result = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkflowRuntimeError('Database node returned an invalid response.') from exc
        if not result.get('ok'):
            raise WorkflowRuntimeError(str(result.get('error') or 'Database query failed.'))
        return result

    result = await execute_workflow_graph(
        graph,
        form_data.input,
        model_runner=model_runner,
        node_runner=node_runner,
        default_model_id=form_data.model_id,
    )
    result['workflow_id'] = workflow.id
    result['workflow_name'] = workflow.name
    result['workflow_version_id'] = version_id
    return result


async def _hydrate_workflow_run_form(
    workflow: WorkflowModel,
    form_data: WorkflowRunForm,
) -> WorkflowRunForm:
    use_draft = form_data.trigger_type in {'manual_test', 'test.editor'} and not form_data.workflow_version_id
    graph = workflow.graph
    version_meta = workflow.meta
    if not use_draft:
        version_id = form_data.workflow_version_id or workflow.default_version_id
        version = await Workflows.get_version_by_id(version_id) if version_id else None
        if version and version.workflow_id == workflow.id:
            graph = version.graph
            version_meta = version.meta
    launch = normalize_launch_contract(version_meta, graph)
    return form_data.model_copy(
        update={
            'input': apply_launch_defaults(launch, form_data.input),
            'model_id': _effective_workflow_model_id(graph, form_data.model_id),
        }
    )


async def execute_chat_workflow(
    request: Request,
    user,
    workflow_request: dict[str, Any],
    form_data: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    workflow_id = str(workflow_request.get('id') or '').strip()
    if not workflow_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Workflow id is required.')
    requested_model_id = str(form_data.get('model') or '').strip() or None
    workflow = await Workflows.get_by_id(workflow_id)
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    requested_version_id = workflow_request.get('versionId') or workflow.default_version_id
    requested_version = await Workflows.get_version_by_id(requested_version_id) if requested_version_id else None
    execution_graph = (
        requested_version.graph
        if requested_version and requested_version.workflow_id == workflow.id
        else workflow.graph
    )
    model_id = _effective_workflow_model_id(execution_graph, requested_model_id)
    channel_context = form_data.get('interact_channel')
    if not isinstance(channel_context, dict):
        channel_context = metadata.get('interact_channel')
    if not isinstance(channel_context, dict):
        channel_context = {}
    channel_id = str(channel_context.get('channelId') or channel_context.get('channel_id') or '').strip() or None
    context = await _workflow_context_for_user(
        user,
        None,
        channel_id=channel_id,
        model_id=model_id,
    )
    check_workflow_access(workflow, user, context, allow_public_template=False)
    if workflow.status != 'published' or not workflow.default_version_id:
        raise HTTPException(status_code=409, detail='Publish this workflow before using it in chat.')

    user_message = metadata.get('user_message') if isinstance(metadata.get('user_message'), dict) else {}
    chat_context = await _workflow_chat_context(user, workflow_request, form_data, metadata)
    input_payload = {
        'message': user_message.get('content') or '',
        'files': ([] if workflow_request.get('parts') else user_message.get('files') or metadata.get('files') or []),
        'parts': workflow_request.get('parts') or [],
        'data': workflow_request.get('data') or {},
        'context': {
            'chat_id': metadata.get('chat_id'),
            'channel_id': channel_id,
            'model_id': model_id,
            **chat_context,
        },
    }
    run_form = WorkflowRunForm(
        input=input_payload,
        trigger_type=str(workflow_request.get('trigger') or 'webui_chat.manual'),
        workflow_version_id=requested_version_id,
        model_id=model_id,
        channel_id=channel_id,
        confirmed=bool(workflow_request.get('confirmed')),
    )
    run_form = await _hydrate_workflow_run_form(workflow, run_form)
    run = await Workflows.insert_run(workflow.id, user.id, run_form)
    try:
        result = await _execute_workflow(request, user, workflow, run_form)
        completed = await Workflows.complete_run(run.id, 'success', output=result)
    except Exception as exc:
        await Workflows.complete_run(run.id, 'error', error=str(exc))
        raise

    outputs = result.get('outputs') if isinstance(result.get('outputs'), list) else []
    text_outputs = [output for output in outputs if isinstance(output, dict) and output.get('type') == 'text']
    content = workflow_outputs_text(text_outputs)
    if not content and outputs:
        content = '工作流已完成，結果如下。'
    metadata['workflow'] = {
        'id': workflow.id,
        'name': workflow.name,
        'versionId': result.get('workflow_version_id') or run_form.workflow_version_id,
        'runId': completed.id if completed else run.id,
        'trigger': run_form.trigger_type,
    }
    return {
        'id': f'workflow-{run.id}',
        'object': 'chat.completion',
        'model': requested_model_id or model_id or 'workflow',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
        'output': workflow_outputs_to_response_items(outputs),
        'usage': result.get('usage') or {},
        'workflow': {
            'id': workflow.id,
            'name': workflow.name,
            'versionId': result.get('workflow_version_id'),
            'runId': completed.id if completed else run.id,
        },
    }


async def workflow_billing_form_data(
    workflow_request: dict[str, Any],
    form_data: dict[str, Any],
) -> dict[str, Any]:
    """Return a billing-only payload scaled to the immutable model-step count."""
    workflow_id = str(workflow_request.get('id') or '').strip()
    workflow = await Workflows.get_by_id(workflow_id) if workflow_id else None
    version_id = workflow_request.get('versionId') or (workflow.default_version_id if workflow else None)
    version = await Workflows.get_version_by_id(version_id) if version_id else None
    if not workflow or workflow.status != 'published' or not version or version.workflow_id != workflow.id:
        raise HTTPException(status_code=400, detail='Published workflow version was not found.')
    nodes = version.graph.get('nodes') if isinstance(version.graph.get('nodes'), list) else []
    model_steps = sum(1 for node in nodes if isinstance(node, dict) and node_semantic_type(node) in RUNTIME_MODEL_TYPES)
    return {**form_data, '_billing_multiplier': max(1, min(8, model_steps))}


def _selector_response(
    workflows: list[WorkflowModel],
    context: WorkflowAccessContext,
    message: str,
    max_items: int,
) -> WorkflowAgentSelectorResponse:
    candidates = [
        candidate for workflow in workflows if (candidate := workflow_agent_candidate(workflow, context, message))
    ]
    decision = decide_workflow_candidates(candidates, max_items)
    return WorkflowAgentSelectorResponse(**decision)


async def select_workflow_for_user_context(
    user,
    message: str,
    *,
    channel_id: str | None = None,
    model_id: str | None = None,
    max_items: int = 3,
) -> WorkflowAgentSelectorResponse:
    """Select an executable published workflow within the caller's ACL context."""
    context = await _workflow_context_for_user(
        user,
        None,
        channel_id=channel_id,
        model_id=model_id,
    )
    result = await Workflows.search(
        user_id=user.id,
        visibility='all',
        skip=0,
        limit=0,
        include_public_templates=False,
        include_shared=True,
    )
    return _selector_response(result.items, context, message, max_items)


async def _preflight_workflow_launch(
    request: Request,
    workflow: WorkflowModel,
    user,
    context: WorkflowAccessContext,
    form_data: WorkflowLaunchPreflightRequest,
) -> WorkflowLaunchPreflightResponse:
    checks: list[WorkflowLaunchCheck] = []
    if workflow.status != 'published' or not workflow.default_version_id:
        launch = normalize_launch_contract(workflow.meta, workflow.graph)
        return WorkflowLaunchPreflightResponse(
            ok=False,
            workflow_id=workflow.id,
            launch=launch,
            checks=[_launch_check('workflow_not_published', 'fail', '工作流尚未發布，不能正式執行。')],
        )

    version_id = form_data.workflow_version_id or workflow.default_version_id
    version = await Workflows.get_version_by_id(version_id)
    if not version or version.workflow_id != workflow.id:
        launch = normalize_launch_contract(workflow.meta, workflow.graph)
        return WorkflowLaunchPreflightResponse(
            ok=False,
            workflow_id=workflow.id,
            workflow_version_id=version_id,
            launch=launch,
            checks=[_launch_check('version_not_found', 'fail', '指定的發布版本不存在或不屬於這個工作流。')],
        )

    effective_model_id = _effective_workflow_model_id(version.graph, form_data.model_id)
    context.model_id = effective_model_id
    if not workflow_acl_allows(workflow, context, allow_public_template=False):
        launch = normalize_launch_contract(version.meta, version.graph)
        return WorkflowLaunchPreflightResponse(
            ok=False,
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            launch=launch,
            effective_model_id=effective_model_id,
            checks=[_launch_check('workflow_access_denied', 'fail', '目前身分、模型或渠道無權執行這個工作流。')],
        )
    checks.extend(
        [
            _launch_check('workflow_published', 'pass', f'將執行已發布版本 v{version.version}。'),
            _launch_check('workflow_access', 'pass', '工作流存取政策允許本次執行。'),
        ]
    )

    launch = normalize_launch_contract(version.meta, version.graph)
    input_check = validate_launch_input(
        launch,
        version.graph,
        form_data.input,
        confirmed=form_data.confirmed,
    )
    non_confirmation_errors = [
        error for error in input_check['errors'] if '需要先確認' not in error
    ]
    if non_confirmation_errors:
        checks.append(_launch_check('input_invalid', 'fail', ' '.join(non_confirmation_errors)))
    else:
        checks.append(_launch_check('input_valid', 'pass', '目前輸入符合工作流規格。'))
    if input_check['requires_confirmation']:
        checks.append(
            _launch_check(
                'confirmation_required',
                'pass' if form_data.confirmed else 'warning',
                '使用者已確認執行外部動作。' if form_data.confirmed else '執行前需要使用者確認外部動作。',
            )
        )

    checks.extend(
        await _workflow_dependency_preflight(
            request,
            workflow,
            user,
            version.graph,
            context,
            form_data.surface,
        )
    )
    failed = any(item.status == 'fail' for item in checks)
    confirmation_pending = input_check['requires_confirmation'] and not form_data.confirmed
    return WorkflowLaunchPreflightResponse(
        ok=not failed and not confirmation_pending,
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        launch=launch,
        effective_model_id=effective_model_id,
        missing_fields=input_check['missing_fields'],
        requires_confirmation=input_check['requires_confirmation'],
        checks=checks,
    )


@router.post('/service/list', response_model=WorkflowListResponse)
async def service_get_workflow_items(
    request: Request,
    form_data: ServiceCompanyListRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    limit = PAGE_ITEM_COUNT
    page = max(1, form_data.page or 1)
    context = _workflow_context_for_service_user(service_user, form_data)

    result = await Workflows.search(
        user_id=service_user.id,
        query=form_data.query,
        visibility=form_data.visibility,
        skip=0,
        limit=0,
        include_public_templates=True,
        include_shared=True,
        db=db,
    )
    items = [
        workflow for workflow in result.items if workflow_acl_allows(workflow, context, allow_public_template=True)
    ]
    return _paginate_workflows(items, page, limit)


@router.post('/service/create', response_model=WorkflowModel)
async def service_create_workflow(
    request: Request,
    form_data: ServiceCompanyWorkflowRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    context = _workflow_context_for_service_user(service_user, form_data)
    payload = form_data.model_dump(
        exclude={
            'companyEmail',
            'companyUserId',
            'companyMemberId',
            'companyMemberRole',
            'channelId',
            'modelId',
        }
    )
    payload['meta'] = normalize_workflow_meta(
        payload.get('meta'),
        owner_user_id=service_user.id,
        company_user_id=context.company_user_id,
        visibility=payload.get('visibility'),
        graph=payload.get('graph'),
    )
    validation = _validate_workflow_configuration(form_data.graph, form_data.visibility, payload['meta'])
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    return await Workflows.insert(service_user.id, WorkflowForm(**payload), db=db)


@router.post('/service/agent/select', response_model=WorkflowAgentSelectorResponse)
async def service_select_agent_workflows(
    request: Request,
    form_data: ServiceWorkflowAgentSelectorRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    context = _workflow_context_for_service_user(service_user, form_data)
    result = await Workflows.search(
        user_id=service_user.id,
        query=None,
        visibility='all',
        skip=0,
        limit=0,
        include_public_templates=False,
        include_shared=True,
        db=db,
    )
    return _selector_response(result.items, context, form_data.message, form_data.maxItems)


@router.post('/service/{id}/preflight', response_model=WorkflowLaunchPreflightResponse)
async def service_preflight_workflow_launch(
    request: Request,
    id: str,
    form_data: ServiceWorkflowLaunchPreflightRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    workflow = await Workflows.get_by_id(id, db=db)
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    context = _workflow_context_for_service_user(service_user, form_data)
    request_form = WorkflowLaunchPreflightRequest(
        input=form_data.input,
        workflow_version_id=form_data.workflow_version_id,
        model_id=form_data.model_id or form_data.modelId,
        channel_id=form_data.channel_id or form_data.channelId,
        surface=form_data.surface,
        confirmed=form_data.confirmed,
    )
    context.model_id = request_form.model_id
    context.channel_id = request_form.channel_id
    return await _preflight_workflow_launch(request, workflow, service_user, context, request_form)


@router.post('/service/{id}/publish', response_model=WorkflowVersionModel)
async def service_publish_workflow(
    request: Request,
    id: str,
    form_data: ServiceCompanyRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, service_user)

    validation = _validate_workflow_configuration(
        workflow.graph,
        workflow.visibility,
        workflow.meta,
        for_publish=True,
    )
    validation['errors'].extend(
        await _validate_semantic_nodes_for_publish(
            workflow,
            _workflow_context_for_service_user(service_user, form_data).company_user_id,
        )
    )
    validation['ok'] = len(validation['errors']) == 0
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    version = await Workflows.publish_version(id, service_user.id, db=db)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return version


@router.post('/service/{id}/run', response_model=WorkflowRunModel)
async def service_run_workflow(
    request: Request,
    id: str,
    form_data: ServiceCompanyRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    context = _workflow_context_for_service_user(service_user, form_data)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, service_user, context, allow_public_template=False)
    run_form = WorkflowRunForm(
        input=form_data.input,
        trigger_type=form_data.trigger_type,
        workflow_version_id=(
            form_data.workflow_version_id
            or (None if form_data.trigger_type in {'manual_test', 'test.editor'} else workflow.default_version_id)
        ),
        model_id=form_data.model_id or form_data.modelId,
        channel_id=form_data.channel_id or form_data.channelId,
        confirmed=form_data.confirmed,
    )
    run_form = await _hydrate_workflow_run_form(workflow, run_form)

    try:
        run = await Workflows.insert_run(id, service_user.id, run_form, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        output = await _execute_workflow(request, service_user, workflow, run_form)
        return await Workflows.complete_run(run.id, 'success', output=output, db=db)
    except Exception as exc:
        completed = await Workflows.complete_run(run.id, 'error', error=str(exc), db=db)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=completed.error)


@router.get('/list', response_model=WorkflowListResponse)
async def get_workflow_items(
    request: Request,
    query: Optional[str] = None,
    visibility: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    limit = PAGE_ITEM_COUNT
    page = max(1, page or 1)
    context = await _workflow_context_for_user(user, db)

    result = await Workflows.search(
        user_id=user.id,
        query=query,
        visibility=visibility,
        skip=0,
        limit=0,
        include_public_templates=True,
        include_shared=True,
        db=db,
    )
    items = [
        workflow for workflow in result.items if workflow_acl_allows(workflow, context, allow_public_template=True)
    ]
    return _paginate_workflows(items, page, limit)


@router.post('/create', response_model=WorkflowModel)
async def create_new_workflow(
    request: Request,
    form_data: WorkflowForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db)
    payload = form_data.model_dump()
    payload['meta'] = normalize_workflow_meta(
        payload.get('meta'),
        owner_user_id=user.id,
        company_user_id=context.company_user_id,
        visibility=payload.get('visibility'),
        graph=payload.get('graph'),
    )
    validation = _validate_workflow_configuration(form_data.graph, form_data.visibility, payload['meta'])
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    return await Workflows.insert(user.id, WorkflowForm(**payload), db=db)


@router.post('/agent/select', response_model=WorkflowAgentSelectorResponse)
async def select_agent_workflows(
    request: Request,
    form_data: WorkflowAgentSelectorRequest,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db, form_data.channelId, form_data.modelId)
    result = await Workflows.search(
        user_id=user.id,
        query=None,
        visibility='all',
        skip=0,
        limit=0,
        include_public_templates=False,
        include_shared=True,
        db=db,
    )
    return _selector_response(result.items, context, form_data.message, form_data.maxItems)


@router.get('/{id}', response_model=WorkflowModel)
async def get_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user, context)
    return workflow


@router.post('/{id}/preflight', response_model=WorkflowLaunchPreflightResponse)
async def preflight_workflow_launch(
    request: Request,
    id: str,
    form_data: WorkflowLaunchPreflightRequest,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    context = await _workflow_context_for_user(user, db, form_data.channel_id, form_data.model_id)
    return await _preflight_workflow_launch(request, workflow, user, context, form_data)


@router.post('/{id}/update', response_model=WorkflowModel)
async def update_workflow_by_id(
    request: Request,
    id: str,
    form_data: WorkflowPatchForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)
    current_meta = workflow.meta if workflow else None
    payload = form_data.model_dump(exclude_unset=True)
    target_visibility = payload.get('visibility', workflow.visibility)
    target_graph = payload.get('graph', workflow.graph)
    normalized_meta = normalize_workflow_meta(
        payload.get('meta', current_meta),
        owner_user_id=workflow.user_id,
        company_user_id=context.company_user_id,
        visibility=target_visibility,
        graph=target_graph,
    )

    if {'graph', 'meta', 'visibility'}.intersection(payload):
        validation = _validate_workflow_configuration(target_graph, target_visibility, normalized_meta)
        if not validation['ok']:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    if 'meta' in payload or 'visibility' in payload:
        payload['meta'] = normalized_meta

    return await Workflows.update_by_id(id, WorkflowPatchForm(**payload), db=db)


@router.post('/{id}/validate', response_model=WorkflowValidateResponse)
async def validate_workflow_by_id(
    request: Request,
    id: str,
    form_data: Optional[WorkflowPatchForm] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user, context)

    graph = form_data.graph if form_data and form_data.graph is not None else workflow.graph
    visibility = form_data.visibility if form_data and form_data.visibility is not None else workflow.visibility
    target_meta = form_data.meta if form_data and form_data.meta is not None else workflow.meta
    normalized_meta = normalize_workflow_meta(
        target_meta,
        owner_user_id=workflow.user_id,
        company_user_id=context.company_user_id,
        visibility=visibility,
        graph=graph,
    )
    return _validate_workflow_configuration(graph, visibility, normalized_meta)


@router.post('/{id}/publish', response_model=WorkflowVersionModel)
async def publish_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)

    validation = _validate_workflow_configuration(
        workflow.graph,
        workflow.visibility,
        workflow.meta,
        for_publish=True,
    )
    publish_context = await _workflow_context_for_user(user, db)
    validation['errors'].extend(await _validate_semantic_nodes_for_publish(workflow, publish_context.company_user_id))
    validation['ok'] = len(validation['errors']) == 0
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    version = await Workflows.publish_version(id, user.id, db=db)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return version


@router.get('/{id}/versions', response_model=list[WorkflowVersionModel])
async def get_workflow_versions(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    context = await _workflow_context_for_user(user, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user, context)
    return await Workflows.get_versions(id, db=db)


@router.post('/{id}/run', response_model=WorkflowRunModel)
async def run_workflow_by_id(
    request: Request,
    id: str,
    form_data: Optional[WorkflowRunForm] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    form_data = form_data or WorkflowRunForm()
    context = await _workflow_context_for_user(user, db, form_data.channel_id, form_data.model_id)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user, context)
    run_form = WorkflowRunForm(
        input=form_data.input,
        trigger_type=form_data.trigger_type,
        workflow_version_id=(
            form_data.workflow_version_id
            or (None if form_data.trigger_type in {'manual_test', 'test.editor'} else workflow.default_version_id)
        ),
        model_id=form_data.model_id,
        channel_id=form_data.channel_id,
        confirmed=form_data.confirmed,
    )
    run_form = await _hydrate_workflow_run_form(workflow, run_form)

    try:
        run = await Workflows.insert_run(id, user.id, run_form, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        output = await _execute_workflow(request, user, workflow, run_form)
        return await Workflows.complete_run(run.id, 'success', output=output, db=db)
    except Exception as exc:
        completed = await Workflows.complete_run(run.id, 'error', error=str(exc), db=db)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=completed.error)


@router.get('/{id}/runs', response_model=list[WorkflowRunModel])
async def get_workflow_runs(
    request: Request,
    id: str,
    limit: Optional[int] = 20,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)
    return await Workflows.get_runs(id, limit=max(1, min(limit or 20, 100)), db=db)


@router.delete('/{id}/delete')
async def delete_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)
    return {'success': await Workflows.delete(id, db=db)}
