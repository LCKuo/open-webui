import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.groups import Groups
from open_webui.models.interact_data_connectors import InteractDataConnectors
from open_webui.models.interact_email import InteractEmail
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
from open_webui.routers.interact_email import (
    EmailSendRequest,
    ensure_email_connector_allowed,
    send_resend_email,
)
from open_webui.semantic_query.contracts import QueryRuntimeContext
from open_webui.semantic_query.errors import SemanticQueryError
from open_webui.semantic_query.service import (
    _connector_allowed as semantic_connector_allowed,
)
from open_webui.semantic_query.service import (
    _dataset_allowed as semantic_dataset_allowed,
)
from open_webui.semantic_query.service import (
    execute_query as execute_semantic_query,
)
from open_webui.storage.provider import Storage
from open_webui.tools.builtin import query_knowledge_files
from open_webui.tools.interact_database import (
    QueryContext as DatabaseQueryContext,
)
from open_webui.tools.interact_database import (
    _connector_denial_reason,
    interact_database_query,
)
from open_webui.utils.access_control import has_permission
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.interact_billing import InteractBillingClient, is_billing_enabled
from open_webui.utils.misc import get_message_list
from open_webui.utils.models import get_all_models, get_filtered_models
from open_webui.utils.workflow_launch import (
    add_guidance_node_to_legacy_graph,
    apply_launch_defaults,
    normalize_launch_contract,
    validate_launch_contract,
    validate_launch_input,
    workflow_configured_model_ids,
    workflow_requires_confirmation,
)
from open_webui.utils.workflow_runtime import (
    RUNTIME_MODEL_TYPES,
    WorkflowPause,
    WorkflowRuntimeError,
    execute_workflow_graph,
    node_semantic_type,
    runtime_unsupported_node_types,
    workflow_outputs_text,
    workflow_outputs_to_response_items,
)
from open_webui.utils.workflows import (
    WorkflowAccessContext,
    decide_workflow_candidates,
    normalize_workflow_meta,
    validate_workflow_agent_policy,
    validate_workflow_graph,
    validate_workflow_visibility,
    workflow_acl,
    workflow_acl_allows,
    workflow_agent_candidate,
    workflow_channel_acl_allows,
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


class ServiceCompanyLifecycleRequest(BaseModel):
    companyEmail: str
    companyUserId: Optional[str] = None
    companyMemberId: Optional[str] = None
    companyMemberRole: Optional[str] = None


class ServiceCompanyListRequest(WorkflowRunForm):
    companyEmail: str
    query: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[Literal['all', 'active', 'draft', 'published', 'archived']] = None
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


class WorkflowResumeRequest(BaseModel):
    decision: Literal['approved', 'rejected', 'selected', 'cancelled']
    value: Any = None
    revision: int = Field(..., ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)


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
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    edges = graph.get('edges') if isinstance(graph.get('edges'), list) else []
    node_types = {str(node.get('id')): node_semantic_type(node) for node in nodes if isinstance(node, dict)}
    incoming = {
        node_id: [
            str(edge.get('source') or '')
            for edge in edges
            if isinstance(edge, dict) and str(edge.get('target') or '') == node_id
        ]
        for node_id in node_types
    }
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get('id') or '')
        node_type = node_semantic_type(node)
        data = node.get('data') if isinstance(node.get('data'), dict) else {}
        config = data.get('config') if isinstance(data.get('config'), dict) else {}
        if node_type == 'condition':
            handles = {
                str(edge.get('sourceHandle') or edge.get('source_handle') or '').lower()
                for edge in edges
                if isinstance(edge, dict) and str(edge.get('source') or '') == node_id
            }
            if not {'true', 'false'}.issubset(handles):
                errors.append(f'Condition node {node_id} requires true and false output connections.')
        elif node_type == 'user_choice' and not config.get('choices') and not config.get('choices_from_path'):
            errors.append(f'User choice node {node_id} requires at least one choice.')
        elif node_type == 'email_send':
            predecessors = [node_types.get(source) for source in incoming.get(node_id, [])]
            if predecessors != ['approval_gate']:
                errors.append(f'Email send node {node_id} must have exactly one approval gate directly before it.')
            if not str(config.get('connector_id') or '').strip():
                errors.append(f'Email send node {node_id} requires an email connector.')
        elif node_type == 'customer_contact_lookup':
            required = ['dataset_id', 'customer_name_field', 'customer_email_field']
            missing = [key for key in required if not str(config.get(key) or '').strip()]
            if missing:
                errors.append(f'Customer contact lookup node {node_id} is missing: {", ".join(missing)}.')
    return {'ok': len(errors) == 0, 'errors': errors, 'warnings': warnings}


async def _validate_semantic_nodes_for_publish(
    workflow: WorkflowModel,
    company_user_id: str | None,
) -> list[str]:
    errors: list[str] = []
    nodes = workflow.graph.get('nodes') if isinstance(workflow.graph, dict) else []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get('id') or 'unknown')
        data = node.get('data') if isinstance(node.get('data'), dict) else {}
        config = data.get('config') if isinstance(data.get('config'), dict) else {}
        if node_semantic_type(node) == 'email_send':
            connector_id = str(config.get('connector_id') or '').strip()
            connector = await InteractEmail.get_connector(connector_id) if connector_id else None
            if not connector or connector.company_user_id != company_user_id:
                errors.append(f'Email send node {node_id} cannot access connector {connector_id or "(missing)"}.')
            elif not connector.enabled or not connector.api_key_encrypted:
                errors.append(f'Email send node {node_id} requires an enabled connector with an API key.')
            elif connector.allowed_workflow_ids and workflow.id not in connector.allowed_workflow_ids:
                errors.append(f'Email connector {connector_id} does not allow workflow {workflow.id}.')
            continue
        if node_semantic_type(node) not in {'semantic_query', 'customer_contact_lookup'}:
            continue
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


async def _schedule_line_rich_menu_refresh(workflow: WorkflowModel) -> None:
    from open_webui.routers.interact_channels import schedule_company_line_rich_menu_refresh

    acl = workflow_acl(workflow)
    company_user_id = str(acl.get('company_user_id') or '').strip() or None
    channel_ids = [str(item).strip() for item in (acl.get('allowed_channel_ids') or []) if str(item).strip()]
    scheduled = await schedule_company_line_rich_menu_refresh(
        company_user_id=company_user_id,
        channel_ids=channel_ids or None,
    )
    if scheduled:
        return
    owner = await Users.get_user_by_id(workflow.user_id)
    if owner and owner.email:
        await schedule_company_line_rich_menu_refresh(
            company_email=owner.email,
            channel_ids=channel_ids or None,
        )


async def _archive_workflow(workflow: WorkflowModel, db: AsyncSession) -> WorkflowModel:
    if workflow.status == 'archived':
        return workflow
    if workflow.status != 'published':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='只有已發布工作流可以停用。')

    try:
        archived = await Workflows.archive(workflow.id, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    await _schedule_line_rich_menu_refresh(archived)
    return archived


async def _activate_workflow(
    workflow: WorkflowModel,
    company_user_id: str | None,
    db: AsyncSession,
) -> WorkflowModel:
    if workflow.status == 'published':
        return workflow
    if workflow.status != 'archived':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='只有已停用工作流可以重新啟用。')
    if not workflow.default_version_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='找不到可重新啟用的發布版本。')

    version = await Workflows.get_version_by_id(workflow.default_version_id, db=db)
    if not version or version.workflow_id != workflow.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='最後發布版本不存在，無法重新啟用。')

    compatible_graph, _ = add_guidance_node_to_legacy_graph(version.graph, version.meta)
    published_workflow = workflow.model_copy(update={'graph': compatible_graph, 'meta': version.meta})
    validation = _validate_workflow_configuration(
        published_workflow.graph,
        published_workflow.visibility,
        published_workflow.meta,
        for_publish=True,
    )
    validation['errors'].extend(await _validate_semantic_nodes_for_publish(published_workflow, company_user_id))
    if validation['errors']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    try:
        activated = await Workflows.activate_default_version(workflow.id, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not activated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    await _schedule_line_rich_menu_refresh(activated)
    return activated


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
                checks.append(_launch_check('model_access', 'pass', f'模型「{effective_model_id}」可供目前帳號使用。'))
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
        elif node_type in {'semantic_query', 'customer_contact_lookup'}:
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
            checks.append(
                _launch_check(
                    'semantic_access', 'pass', f'資料集「{dataset.get("name") or dataset_id}」已發布且允許本次執行。'
                )
            )
        elif node_type == 'email_send':
            connector_id = str(config.get('connector_id') or '').strip()
            connector = await InteractEmail.get_connector(connector_id) if connector_id else None
            if not connector:
                checks.append(
                    _launch_check('email_connector_not_found', 'fail', '寄信 Connector 不存在或屬於其他企業。')
                )
            else:
                try:
                    ensure_email_connector_allowed(
                        connector,
                        {
                            'company_user_id': context.company_user_id,
                            'company_member_id': context.company_member_id,
                            'company_member_role': context.company_member_role,
                            'group_ids': list(context.group_ids),
                            'user_id': context.user_id,
                        },
                        workflow.id,
                        context.channel_id,
                    )
                    if not connector.api_key_encrypted:
                        raise HTTPException(status_code=409, detail='寄信 Connector 尚未設定 API Key。')
                except HTTPException as error:
                    checks.append(
                        _launch_check(
                            'email_connector_access_denied',
                            'fail',
                            str(error.detail or '寄信 Connector 不允許本次執行。'),
                        )
                    )
                else:
                    checks.append(
                        _launch_check(
                            'email_connector_access',
                            'pass',
                            f'寄信 Connector「{connector.name}」允許目前成員與渠道使用。',
                        )
                    )
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
            checks.append(
                _launch_check('database_access', 'pass', f'資料庫連接器「{connector.name}」已啟用並允許本次執行。')
            )
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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _email_draft_contact_context(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source.get(key)
        for key in ('customer_id', 'customer_name', 'contact_name', 'source')
        if source.get(key) is not None
    }


def _customer_request_context(source: Any, original_message: str = '') -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    context: dict[str, Any] = {}
    request = str(source.get('request') or '').strip()
    if request:
        context['request'] = request
    cc = source.get('cc')
    if isinstance(cc, str):
        cc = [item.strip() for item in cc.split(',') if item.strip()]
    if isinstance(cc, list):
        normalized_message = original_message.casefold()
        context['cc'] = [
            address
            for item in cc
            if (address := str(item).strip().lower())
            and (not original_message or address.casefold() in normalized_message)
        ]
    return context


def _email_knowledge_context(value: Any, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    chunks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = str(item.get('content') or '').strip()
        if not content:
            continue
        chunks.append(
            {
                'content': content[:4000],
                'source': str(item.get('source') or '知識庫')[:200],
            }
        )
        if len(chunks) >= max(1, min(10, limit)):
            break
    return chunks


def _email_compose_context(incoming: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    knowledge = _email_knowledge_context(incoming.get('knowledge') if isinstance(incoming, dict) else None)
    current = incoming
    for _ in range(8):
        if not isinstance(current, dict):
            break
        selected = current.get('selected')
        if isinstance(selected, dict):
            return (
                {
                    'status': 'found',
                    'customer_id': selected.get('id'),
                    'customer_name': selected.get('name'),
                    'contact_name': selected.get('contact_name'),
                    'email': selected.get('email'),
                    'request': selected.get('request'),
                    'cc': selected.get('cc'),
                    'source': {'selected_by_user': True},
                },
                knowledge,
            )
        if current.get('status'):
            return current, knowledge
        if 'value' not in current:
            break
        current = current.get('value')
    return {}, knowledge


def _normalize_email_draft_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def _validate_email_draft_content(subject: Any, text: Any, request: Any = '') -> tuple[str, str]:
    normalized_subject = str(subject or '').strip()
    normalized_text = str(text or '').strip()
    if not normalized_subject or not normalized_text:
        raise WorkflowRuntimeError('AI 信件草稿缺少主旨或正文，已停止寄送。')
    if len(normalized_subject) > 300 or len(normalized_text) > 50000:
        raise WorkflowRuntimeError('AI 信件草稿長度不合理，已停止寄送。')

    request_text = _normalize_email_draft_text(request)
    body_text = _normalize_email_draft_text(normalized_text)
    if request_text and (
        body_text == request_text or (body_text.startswith(request_text) and len(body_text) <= len(request_text) + 80)
    ):
        raise WorkflowRuntimeError('AI 未能產生信件正文，系統已阻止將操作指令當成郵件寄出。')
    return normalized_subject, normalized_text


def _parse_email_draft(raw: Any, request: Any = '') -> tuple[str, str]:
    cleaned = re.sub(
        r'^```(?:json)?\s*|\s*```$',
        '',
        str(raw or '').strip(),
        flags=re.IGNORECASE,
    )
    try:
        generated = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise WorkflowRuntimeError('AI 信件草稿不是有效的 JSON，已停止寄送。') from exc
    if not isinstance(generated, dict):
        raise WorkflowRuntimeError('AI 信件草稿格式不正確，已停止寄送。')

    candidates = [generated]
    for key in ('email', 'draft', 'message'):
        nested = generated.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)

    subject = next(
        (
            value
            for candidate in candidates
            for key in ('subject', 'title', '主旨')
            if isinstance((value := candidate.get(key)), str) and value.strip()
        ),
        '',
    )
    text = next(
        (
            value
            for candidate in candidates
            for key in ('text', 'body', 'content', '正文', '內容')
            if isinstance((value := candidate.get(key)), str) and value.strip()
        ),
        '',
    )
    return _validate_email_draft_content(subject, text, request)


def _customer_contact_query_plan(
    dataset_id: str,
    dimensions: list[str],
    customer_name_field: str,
    customer_name: str,
    limit: int,
) -> dict[str, Any]:
    return {
        'version': '1',
        'datasetId': dataset_id,
        'dimensions': dimensions,
        'measures': [],
        'metrics': [],
        'filters': {
            'operator': 'and',
            'conditions': [
                {
                    'fieldId': customer_name_field,
                    'operator': 'contains',
                    'value': customer_name,
                }
            ],
        },
        'orderBy': [],
        'limit': limit,
    }


async def _workflow_part_data_url(part: dict[str, Any], user) -> str | None:
    url = str(part.get('url') or '').strip()
    if url:
        return url
    file_id = str(part.get('fileId') or part.get('id') or '').strip()
    if not file_id:
        return None
    file = await Files.get_file_by_id(file_id)
    if not file:
        return None
    if file.user_id != user.id and user.role != 'admin' and not await has_access_to_file(file.id, 'read', user):
        return None
    file_path = await asyncio.to_thread(Storage.get_file, file.path)
    payload = await asyncio.to_thread(Path(file_path).read_bytes)
    mime_type = str(
        part.get('mimeType')
        or part.get('content_type')
        or (file.meta or {}).get('content_type')
        or mimetypes.guess_type(file.filename)[0]
        or 'application/octet-stream'
    ).split(';', 1)[0]
    return f'data:{mime_type};base64,{base64.b64encode(payload).decode("ascii")}'


async def _workflow_multimodal_content(
    prompt: str,
    parts: list[dict[str, Any]],
    user,
) -> str | list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{'type': 'text', 'text': prompt}]
    for part in parts:
        if not isinstance(part, dict) or part.get('type') == 'text':
            continue
        data_url = await _workflow_part_data_url(part, user)
        if not data_url:
            continue
        part_type = str(part.get('type') or 'file')
        filename = str(part.get('filename') or part.get('name') or f'attachment-{part_type}')
        if part_type == 'image':
            content.append({'type': 'image_url', 'image_url': {'url': data_url}})
        elif part_type == 'audio':
            mime_type = data_url[5 : data_url.find(';')]
            audio_format = mime_type.rsplit('/', 1)[-1].lower()
            audio_format = {'mpeg': 'mp3', 'x-wav': 'wav', 'mp4': 'm4a'}.get(
                audio_format,
                audio_format,
            )
            content.append(
                {
                    'type': 'input_audio',
                    'input_audio': {
                        'data': data_url.split(',', 1)[1],
                        'format': audio_format,
                    },
                }
            )
        elif part_type == 'video':
            content.append({'type': 'video_url', 'video_url': {'url': data_url}})
        else:
            content.append(
                {
                    'type': 'file',
                    'file': {'filename': filename, 'file_data': data_url},
                }
            )
    return content if len(content) > 1 else prompt


async def _execute_workflow(
    request: Request,
    user,
    workflow: WorkflowModel,
    form_data: WorkflowRunForm,
    run_id: str | None = None,
    resume_state: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
    access_context_override: WorkflowAccessContext | None = None,
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
    runtime_context = access_context_override or await _workflow_context_for_user(
        user,
        None,
        channel_id=form_data.channel_id,
        model_id=form_data.model_id,
    )

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
        content = await _workflow_multimodal_content(prompt, parts, user)
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
        if node_type not in {
            'knowledge_query',
            'database_query',
            'semantic_query',
            'structured_extract',
            'customer_contact_lookup',
            'email_compose',
            'email_send',
            'email_delivery_status',
        }:
            raise WorkflowRuntimeError(f'No secure runtime is registered for {node_type}.')
        if node_type == 'structured_extract':
            schema = config.get('schema') if isinstance(config.get('schema'), dict) else {}
            properties = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}
            required = schema.get('required') if isinstance(schema.get('required'), list) else []
            extraction_prompt = (
                str(config.get('instruction') or 'Extract the requested fields from the user message.')
                + '\nReturn one JSON object only. Do not add fields.\nJSON schema:\n'
                + json.dumps({'type': 'object', 'properties': properties, 'required': required}, ensure_ascii=False)
                + '\nInput:\n'
                + (
                    json.dumps(incoming, ensure_ascii=False, default=str)
                    if incoming is not None
                    else str(workflow_input.get('message') or '')
                )
            )
            extracted = await model_runner(
                extraction_prompt,
                'You are a strict JSON extraction engine. Never infer an email address that is not present.',
                str(config.get('model_id') or form_data.model_id or '') or None,
                [],
            )
            raw = str(extracted.get('text') or '').strip()
            if raw.startswith('```'):
                raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.IGNORECASE)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise WorkflowRuntimeError('Structured extraction did not return valid JSON.') from exc
            if not isinstance(value, dict):
                raise WorkflowRuntimeError('Structured extraction must return a JSON object.')
            value = {key: value.get(key) for key in properties}
            missing = [key for key in required if value.get(key) in (None, '', [])]
            if missing:
                raise WorkflowRuntimeError('Missing required extracted fields: ' + ', '.join(missing))
            return value

        if node_type == 'customer_contact_lookup':
            dataset_id = str(config.get('dataset_id') or '').strip()
            name_field = str(config.get('customer_name_field') or '').strip()
            email_field = str(config.get('customer_email_field') or '').strip()
            if not dataset_id or not name_field or not email_field:
                raise WorkflowRuntimeError('Customer contact lookup requires a dataset, name field, and email field.')
            query_path = str(config.get('query_path') or 'customer_name')
            query_source = incoming.get('value') if isinstance(incoming, dict) and 'value' in incoming else incoming
            request_context = _customer_request_context(
                query_source,
                str(workflow_input.get('message') or ''),
            )
            customer_name = query_source
            for part in query_path.split('.'):
                customer_name = customer_name.get(part) if isinstance(customer_name, dict) else None
            customer_name = str(customer_name or workflow_input.get('data', {}).get(query_path) or '').strip()
            if not customer_name:
                raise WorkflowRuntimeError('Customer name is required before contact lookup.')
            dimensions = list(
                dict.fromkeys(
                    [
                        str(config.get('customer_id_field') or '').strip(),
                        name_field,
                        str(config.get('contact_name_field') or '').strip(),
                        email_field,
                        str(config.get('primary_field') or '').strip(),
                        str(config.get('opt_out_field') or '').strip(),
                    ]
                )
            )
            dimensions = [item for item in dimensions if item]
            access_context = runtime_context
            try:
                query_result = await execute_semantic_query(
                    _customer_contact_query_plan(
                        dataset_id,
                        dimensions,
                        name_field,
                        customer_name,
                        max(2, min(20, int(config.get('max_candidates') or 10))),
                    ),
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
                    unmasked_field_ids={email_field},
                )
            except SemanticQueryError as error:
                raise WorkflowRuntimeError(error.public()['message']) from error
            rows = query_result.get('rows') if isinstance(query_result, dict) else []
            if not isinstance(rows, list):
                rows = []
            exact = [row for row in rows if str(row.get(name_field) or '').strip().lower() == customer_name.lower()]
            candidates = exact or rows
            valid = []
            for row in candidates:
                email = str(row.get(email_field) or '').strip().lower()
                opted_out = (
                    _as_bool(row.get(str(config.get('opt_out_field') or ''))) if config.get('opt_out_field') else False
                )
                if email and '@' in email and not opted_out:
                    valid.append(row)
            if not rows:
                return {
                    'status': 'not_found',
                    'query': customer_name,
                    'candidates': [],
                    **request_context,
                }
            if not valid:
                return {
                    'status': 'email_missing',
                    'query': customer_name,
                    'candidates': rows,
                    **request_context,
                }
            if len(valid) > 1:
                return {
                    'status': 'ambiguous',
                    'query': customer_name,
                    **request_context,
                    'candidates': [
                        {
                            'id': row.get(str(config.get('customer_id_field') or '')) or row.get(email_field),
                            'name': row.get(name_field),
                            'contact_name': row.get(str(config.get('contact_name_field') or '')),
                            'email': row.get(email_field),
                            **request_context,
                        }
                        for row in valid
                    ],
                }
            row = valid[0]
            return {
                'status': 'found',
                'query': customer_name,
                'customer_id': row.get(str(config.get('customer_id_field') or '')),
                'customer_name': row.get(name_field),
                'contact_name': row.get(str(config.get('contact_name_field') or '')),
                'email': str(row.get(email_field) or '').lower(),
                'source': {'dataset_id': dataset_id},
                **request_context,
            }

        if node_type == 'email_compose':
            source, knowledge_context = _email_compose_context(incoming)
            if not source:
                raise WorkflowRuntimeError('Customer contact lookup result is missing before email drafting.')
            if config.get('require_knowledge') and not knowledge_context:
                raise WorkflowRuntimeError('找不到足夠的授權知識庫資料，本次沒有產生或寄送郵件。')
            if source.get('status') and source.get('status') != 'found':
                raise WorkflowRuntimeError(f'Customer contact lookup is not ready: {source.get("status")}.')

            def render(template: str) -> str:
                values = {**workflow_input.get('data', {}), **source, 'message': workflow_input.get('message', '')}
                return re.sub(
                    r'\{\{\s*([^{}]+?)\s*\}\}',
                    lambda match: str(values.get(match.group(1).strip(), match.group(0))),
                    template,
                )

            to = [str(source.get('email') or '').strip().lower()]
            cc_values = (
                source.get('cc')
                or workflow_input.get('data', {}).get(str(config.get('cc_input_key') or 'cc'))
                or config.get('default_cc')
                or []
            )
            if isinstance(cc_values, str):
                cc_values = [item.strip() for item in cc_values.split(',') if item.strip()]
            subject = render(str(config.get('subject_template') or '關於 {{customer_name}} 的通知'))
            text_body = render(str(config.get('text_template') or workflow_input.get('message') or ''))
            if config.get('use_model'):
                request_text = source.get('request') or workflow_input.get('message') or ''
                compose_prompt = (
                    str(config.get('instruction') or 'Draft a concise professional email.')
                    + '\nReturn exactly one JSON object with two non-empty string fields: '
                    + '{"subject":"...","text":"..."}. '
                    + 'The text field must be the finished recipient-facing email, never the user instruction. '
                    + 'Recipient data must not be changed.\nContext:\n'
                    + json.dumps(
                        {
                            'request': request_text,
                            'contact': _email_draft_contact_context(source),
                            'knowledge': knowledge_context,
                        },
                        ensure_ascii=False,
                    )
                )
                draft_error: WorkflowRuntimeError | None = None
                for attempt in range(2):
                    draft = await model_runner(
                        compose_prompt
                        + (
                            '\nYour previous response was invalid. Return only the required JSON object; '
                            + 'do not echo the request.'
                            if attempt
                            else ''
                        ),
                        'Return strict JSON. Do not add recipients, promises, prices, or facts absent from context.',
                        str(config.get('model_id') or form_data.model_id or '') or None,
                        [],
                    )
                    try:
                        subject, text_body = _parse_email_draft(draft.get('text'), request_text)
                        draft_error = None
                        break
                    except WorkflowRuntimeError as exc:
                        draft_error = exc
                if draft_error:
                    raise WorkflowRuntimeError(
                        'AI 連續兩次未能產生安全且完整的信件草稿，本次工作流已停止，沒有寄出郵件。'
                    ) from draft_error
            subject, text_body = _validate_email_draft_content(
                subject,
                text_body,
                source.get('request') or workflow_input.get('message'),
            )
            if not to[0] or '@' not in to[0]:
                raise WorkflowRuntimeError('Resolved customer contact does not contain a valid email address.')
            return {
                'to': to,
                'cc': cc_values,
                'subject': subject,
                'text': text_body,
                'html': None,
                'customer': source,
            }

        if node_type == 'email_send':
            if not isinstance(incoming, dict) or not isinstance(incoming.get('_approval'), dict):
                raise WorkflowRuntimeError('Email send requires an approval gate immediately before it.')
            approval = incoming['_approval']
            email_payload = incoming.get('value')
            if not approval.get('approved') or not isinstance(email_payload, dict):
                raise WorkflowRuntimeError('Email approval is missing or invalid.')
            customer = email_payload.get('customer') if isinstance(email_payload.get('customer'), dict) else {}
            _validate_email_draft_content(
                email_payload.get('subject'),
                email_payload.get('text'),
                customer.get('request'),
            )
            connector_id = str(config.get('connector_id') or '').strip()
            connector = await InteractEmail.get_connector(connector_id)
            access_context = runtime_context
            if not connector:
                raise WorkflowRuntimeError('Email connector was not found.')
            delivery = await send_resend_email(
                connector,
                {
                    'company_user_id': access_context.company_user_id,
                    'company_member_id': access_context.company_member_id,
                    'company_member_role': access_context.company_member_role,
                    'group_ids': list(access_context.group_ids),
                    'user_id': user.id,
                },
                EmailSendRequest(
                    connector_id=connector_id,
                    to=email_payload.get('to') or [],
                    cc=email_payload.get('cc') or [],
                    subject=str(email_payload.get('subject') or ''),
                    text=email_payload.get('text'),
                    html=email_payload.get('html'),
                    workflow_id=workflow.id,
                    workflow_run_id=run_id,
                    channel_id=form_data.channel_id,
                    idempotency_key='wf-'
                    + hashlib.sha256(
                        (
                            f'{workflow.id}|{run_id or "adhoc"}|'
                            f'{config.get("_runtime_node_id") or "email"}|'
                            f'{config.get("idempotency_scope") or "send"}'
                        ).encode()
                    ).hexdigest(),
                    payload_hash=str(approval.get('payload_hash') or ''),
                ),
            )
            return delivery.model_dump()

        if node_type == 'email_delivery_status':
            return incoming
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
            if config.get('preserve_input'):
                return {'value': incoming, 'knowledge': result}
            return result

        access_context = runtime_context
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
        resume_state=resume_state,
        resume=resume,
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


async def _save_workflow_pause(
    run: WorkflowRunModel,
    workflow: WorkflowModel,
    context: WorkflowAccessContext,
    pause: WorkflowPause,
    db: AsyncSession | None = None,
) -> WorkflowRunModel:
    checkpoint = await InteractEmail.save_checkpoint(
        workflow_run_id=run.id,
        company_user_id=str(context.company_user_id or ''),
        workflow_id=workflow.id,
        node_id=pause.node_id,
        wait_type=pause.wait_type,
        state=pause.state,
        prompt=pause.prompt,
        payload_hash=pause.payload_hash,
    )
    wait_status = 'waiting_approval' if pause.wait_type == 'approval' else 'waiting_input'
    card = {
        'type': 'card',
        'title': pause.prompt.get('title') or ('等待核准' if pause.wait_type == 'approval' else '等待輸入'),
        'body': pause.prompt.get('message') or '',
        'data': {
            'checkpoint_id': checkpoint['id'],
            'workflow_id': workflow.id,
            'run_id': run.id,
            'wait_type': pause.wait_type,
            'revision': checkpoint['revision'],
            'prompt': pause.prompt,
        },
        'actions': (
            [
                {
                    'type': 'workflow_resume',
                    'label': pause.prompt.get('confirm_label') or '確認',
                    'decision': 'approved',
                },
                {
                    'type': 'workflow_resume',
                    'label': pause.prompt.get('cancel_label') or '取消',
                    'decision': 'rejected',
                },
            ]
            if pause.wait_type == 'approval'
            else [
                {
                    'type': 'workflow_resume',
                    'label': str(choice.get('label') or choice.get('value')),
                    'decision': 'selected',
                    'value': choice.get('value'),
                }
                for choice in pause.prompt.get('choices') or []
                if isinstance(choice, dict)
            ]
        ),
    }
    output = {
        'workflow_id': workflow.id,
        'workflow_name': workflow.name,
        'workflow_version_id': run.workflow_version_id,
        'status': wait_status,
        'checkpoint': card['data'],
        'outputs': [card],
        'usage': pause.state.get('usage') or {},
    }
    return await Workflows.complete_run(run.id, wait_status, output=output, db=db)


def _verified_channel_access_context(
    request: Request,
    user,
    channel_context: dict[str, Any],
    channel_id: str | None,
    model_id: str | None,
) -> WorkflowAccessContext | None:
    if not getattr(request.state, 'interact_channel_runtime', False):
        return None
    if channel_context.get('identitySource') != 'line-binding':
        return None
    company_user_id = str(channel_context.get('companyUserId') or '').strip()
    member_role = str(channel_context.get('companyMemberRole') or '').strip().lower()
    if not company_user_id or member_role not in {'owner', 'admin', 'member'}:
        return None
    return WorkflowAccessContext(
        user_id=user.id if member_role == 'owner' else None,
        role=user.role if member_role == 'owner' else 'user',
        company_user_id=company_user_id,
        company_member_id=str(channel_context.get('companyMemberId') or '').strip() or None,
        company_member_role=member_role,
        group_ids={str(item) for item in channel_context.get('groupIds') or [] if str(item)},
        channel_id=channel_id,
        model_id=model_id,
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
    context = _verified_channel_access_context(
        request,
        user,
        channel_context,
        channel_id,
        model_id,
    ) or await _workflow_context_for_user(
        user,
        None,
        channel_id=channel_id,
        model_id=model_id,
    )
    if channel_id:
        if not workflow_channel_acl_allows(workflow, context):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)
    else:
        check_workflow_access(workflow, user, context, allow_public_template=False)
    if workflow.status != 'published' or not workflow.default_version_id:
        raise HTTPException(status_code=409, detail='Publish this workflow before using it in chat.')

    user_message = metadata.get('user_message') if isinstance(metadata.get('user_message'), dict) else {}
    chat_context = await _workflow_chat_context(user, workflow_request, form_data, metadata)
    workflow_parts = workflow_request.get('parts') if isinstance(workflow_request.get('parts'), list) else []
    part_mime_defaults = {
        'image': 'image/*',
        'video': 'video/*',
        'audio': 'audio/*',
    }
    part_files = [
        {
            'name': part.get('filename') or f'channel-{part.get("type") or "file"}',
            'filename': part.get('filename') or f'channel-{part.get("type") or "file"}',
            'content_type': part.get('content_type')
            or part.get('mimeType')
            or part_mime_defaults.get(str(part.get('type') or ''), ''),
            'size': part.get('size') or 0,
            'platformFileId': part.get('platformFileId'),
            'id': part.get('fileId') or part.get('id'),
            'fileId': part.get('fileId') or part.get('id'),
        }
        for part in workflow_parts
        if isinstance(part, dict)
    ]
    input_payload = {
        'message': user_message.get('content') or '',
        'files': part_files or user_message.get('files') or metadata.get('files') or [],
        'parts': workflow_parts,
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
        result = await _execute_workflow(
            request,
            user,
            workflow,
            run_form,
            run_id=run.id,
            access_context_override=context,
        )
        completed = await Workflows.complete_run(run.id, 'success', output=result)
    except WorkflowPause as pause:
        completed = await _save_workflow_pause(run, workflow, context, pause)
        result = completed.output or {}
    except Exception as exc:
        await Workflows.complete_run(run.id, 'error', error=str(exc))
        raise

    outputs = result.get('outputs') if isinstance(result.get('outputs'), list) else []
    text_outputs = [output for output in outputs if isinstance(output, dict) and output.get('type') == 'text']
    content = workflow_outputs_text(text_outputs)
    if result.get('status') in {'waiting_input', 'waiting_approval'}:
        content = ''
    elif not content and outputs:
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
    candidates: list[dict[str, Any]] = []
    requested_model_id = context.model_id
    for workflow in workflows:
        context.model_id = _effective_workflow_model_id(workflow.graph, requested_model_id)
        if context.channel_id and not workflow_channel_acl_allows(workflow, context):
            continue
        candidate = workflow_agent_candidate(workflow, context, message)
        if candidate:
            candidates.append(candidate)
    context.model_id = requested_model_id
    decision = decide_workflow_candidates(candidates, max_items)
    return WorkflowAgentSelectorResponse(**decision)


async def select_workflow_for_user_context(
    user,
    message: str,
    *,
    channel_id: str | None = None,
    model_id: str | None = None,
    max_items: int = 3,
    access_context: WorkflowAccessContext | None = None,
) -> WorkflowAgentSelectorResponse:
    """Select an executable published workflow within the caller's ACL context."""
    context = access_context or await _workflow_context_for_user(
        user,
        None,
        channel_id=channel_id,
        model_id=model_id,
    )
    context.channel_id = channel_id
    context.model_id = model_id
    result = await Workflows.search(
        user_id=user.id,
        visibility='all',
        skip=0,
        limit=0,
        include_public_templates=False,
        include_shared=True,
    )
    return _selector_response(result.items, context, message, max_items)


async def list_channel_workflows_for_user_context(
    user,
    *,
    channel_id: str,
    model_id: str | None = None,
    limit: int = 13,
    access_context: WorkflowAccessContext | None = None,
) -> list[dict[str, Any]]:
    """Return published workflows that are executable from an external channel."""
    context = access_context or await _workflow_context_for_user(
        user,
        None,
        channel_id=channel_id,
        model_id=model_id,
    )
    context.channel_id = channel_id
    context.model_id = model_id
    result = await Workflows.search(
        user_id=user.id,
        visibility='all',
        workflow_status='published',
        skip=0,
        limit=0,
        include_public_templates=False,
        include_shared=True,
    )
    options: list[dict[str, Any]] = []
    for workflow in result.items:
        if workflow.status != 'published' or not workflow.default_version_id:
            continue
        version = await Workflows.get_version_by_id(workflow.default_version_id)
        if not version or version.workflow_id != workflow.id:
            continue
        context.model_id = _effective_workflow_model_id(version.graph, model_id)
        if not workflow_channel_acl_allows(workflow, context):
            continue
        if runtime_unsupported_node_types(version.graph):
            continue
        launch = normalize_launch_contract(version.meta, version.graph)
        acl = workflow_acl(workflow)
        try:
            priority = max(-100, min(100, int(acl.get('agent_selection_priority') or 0)))
        except (TypeError, ValueError):
            priority = 0
        options.append(
            {
                'id': workflow.id,
                'versionId': version.id,
                'name': workflow.name,
                'description': workflow.description,
                'buttonLabel': str(launch.get('buttonLabel') or workflow.name).strip() or workflow.name,
                'launchMode': launch['mode'],
                'instruction': launch['instruction'],
                'inputSchema': launch['inputSchema'],
                'defaultInput': launch['defaultInput'],
                'fileRules': launch['fileRules'],
                'requiresConfirmation': workflow_requires_confirmation(launch, version.graph),
                'priority': priority,
                'updatedAt': workflow.updated_at,
            }
        )

    options.sort(
        key=lambda item: (item['priority'], item['updatedAt'], item['name']),
        reverse=True,
    )
    return options[:limit] if limit > 0 else options


async def list_instant_workflows_for_user_context(
    user,
    *,
    channel_id: str,
    model_id: str | None = None,
    limit: int = 13,
) -> list[dict[str, Any]]:
    """Backward-compatible instant-only view of channel workflows."""
    options = await list_channel_workflows_for_user_context(
        user,
        channel_id=channel_id,
        model_id=model_id,
        limit=0,
    )
    instant = [item for item in options if item.get('launchMode') == 'instant']
    return instant[:limit] if limit > 0 else instant


async def resolve_channel_workflow_for_user_context(
    user,
    workflow_id: str,
    version_id: str,
    *,
    channel_id: str,
    model_id: str | None = None,
    access_context: WorkflowAccessContext | None = None,
) -> dict[str, Any] | None:
    """Resolve the current published channel workflow and recheck its ACL."""
    options = await list_channel_workflows_for_user_context(
        user,
        channel_id=channel_id,
        model_id=model_id,
        limit=0,
        access_context=access_context,
    )
    return next(
        (option for option in options if option['id'] == workflow_id and option['versionId'] == version_id),
        None,
    )


async def resolve_instant_workflow_for_user_context(
    user,
    workflow_id: str,
    version_id: str,
    *,
    channel_id: str,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a LINE postback only when its published version is still current."""
    options = await list_instant_workflows_for_user_context(
        user,
        channel_id=channel_id,
        model_id=model_id,
        limit=0,
    )
    return next(
        (option for option in options if option['id'] == workflow_id and option['versionId'] == version_id),
        None,
    )


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
    access_allowed = (
        workflow_channel_acl_allows(workflow, context)
        if context.channel_id
        else workflow_acl_allows(workflow, context, allow_public_template=False)
    )
    if not access_allowed:
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
    non_confirmation_errors = [error for error in input_check['errors'] if '需要先確認' not in error]
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
        workflow_status=form_data.status,
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
    published = await Workflows.get_by_id(id, db=db)
    if published:
        await _schedule_line_rich_menu_refresh(published)
    return version


@router.post('/service/{id}/archive', response_model=WorkflowModel)
async def service_archive_workflow(
    request: Request,
    id: str,
    form_data: ServiceCompanyLifecycleRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, service_user)
    return await _archive_workflow(workflow, db)


@router.post('/service/{id}/activate', response_model=WorkflowModel)
async def service_activate_workflow(
    request: Request,
    id: str,
    form_data: ServiceCompanyLifecycleRequest,
    authorization: str | None = Header(default=None),
    x_interact_service_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_async_session),
):
    _require_service_token(authorization, x_interact_service_token)
    service_user = await _resolve_service_user(form_data.companyEmail, db)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, service_user)
    context = _workflow_context_for_service_user(service_user, form_data)
    return await _activate_workflow(workflow, context.company_user_id, db)


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
        output = await _execute_workflow(request, service_user, workflow, run_form, run_id=run.id)
        return await Workflows.complete_run(run.id, 'success', output=output, db=db)
    except WorkflowPause as pause:
        return await _save_workflow_pause(run, workflow, context, pause, db=db)
    except Exception as exc:
        completed = await Workflows.complete_run(run.id, 'error', error=str(exc), db=db)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=completed.error)


@router.get('/list', response_model=WorkflowListResponse)
async def get_workflow_items(
    request: Request,
    query: Optional[str] = None,
    visibility: Optional[str] = None,
    workflow_status: Optional[Literal['all', 'active', 'draft', 'published', 'archived']] = Query(
        default=None,
        alias='status',
    ),
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
        workflow_status=workflow_status,
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

    updated = await Workflows.update_by_id(id, WorkflowPatchForm(**payload), db=db)
    if updated:
        await _schedule_line_rich_menu_refresh(workflow)
        await _schedule_line_rich_menu_refresh(updated)
    return updated


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
    published = await Workflows.get_by_id(id, db=db)
    if published:
        await _schedule_line_rich_menu_refresh(published)
    return version


@router.post('/{id}/archive', response_model=WorkflowModel)
async def archive_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)
    return await _archive_workflow(workflow, db)


@router.post('/{id}/activate', response_model=WorkflowModel)
async def activate_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)
    context = await _workflow_context_for_user(user, db)
    return await _activate_workflow(workflow, context.company_user_id, db)


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
        output = await _execute_workflow(request, user, workflow, run_form, run_id=run.id)
        return await Workflows.complete_run(run.id, 'success', output=output, db=db)
    except WorkflowPause as pause:
        return await _save_workflow_pause(run, workflow, context, pause, db=db)
    except Exception as exc:
        completed = await Workflows.complete_run(run.id, 'error', error=str(exc), db=db)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=completed.error)


async def _resume_workflow_run_internal(
    request: Request,
    workflow: WorkflowModel,
    run: WorkflowRunModel,
    form_data: WorkflowResumeRequest,
    user,
    context: WorkflowAccessContext,
    actor_id: str,
    db: AsyncSession | None = None,
) -> WorkflowRunModel:
    run_id = run.id
    checkpoint = await InteractEmail.get_checkpoint(run_id)
    if not checkpoint or checkpoint.get('company_user_id') != str(context.company_user_id or ''):
        raise HTTPException(status_code=404, detail='Workflow checkpoint not found.')
    if checkpoint.get('workflow_id') != workflow.id or run.workflow_id != workflow.id:
        raise HTTPException(status_code=404, detail='Workflow checkpoint not found.')
    wait_type = str(checkpoint.get('wait_type') or '')
    if wait_type == 'approval' and form_data.decision not in {'approved', 'rejected', 'cancelled'}:
        raise HTTPException(status_code=400, detail='This prompt requires an approval or cancellation decision.')
    if wait_type == 'input' and form_data.decision not in {'selected', 'cancelled'}:
        raise HTTPException(status_code=400, detail='This prompt requires one of the displayed choices.')
    consumed = await InteractEmail.consume_checkpoint(
        workflow_run_id=run_id,
        company_user_id=str(context.company_user_id or ''),
        actor_id=actor_id,
        decision=form_data.decision,
        expected_revision=form_data.revision,
        reason=form_data.reason,
    )
    if not consumed:
        raise HTTPException(status_code=409, detail='This workflow prompt expired or was already handled.')
    if form_data.decision in {'rejected', 'cancelled'}:
        return await Workflows.complete_run(
            run_id,
            'cancelled',
            output={
                'status': 'cancelled',
                'workflow_id': workflow.id,
                'workflow_name': workflow.name,
                'outputs': [{'type': 'text', 'text': '已取消，未執行寄送或其他外部操作。'}],
            },
            db=db,
        )
    original_context = run.input.get('context') if isinstance(run.input, dict) else {}
    original_context = original_context if isinstance(original_context, dict) else {}
    run_form = WorkflowRunForm(
        input=run.input or {},
        trigger_type=run.trigger_type,
        workflow_version_id=run.workflow_version_id,
        model_id=str(original_context.get('model_id') or context.model_id or '').strip() or None,
        channel_id=str(original_context.get('channel_id') or context.channel_id or '').strip() or None,
        confirmed=True,
    )
    resume = {
        'node_id': consumed['node_id'],
        'decision': form_data.decision,
        'value': form_data.value,
        'payload_hash': consumed.get('payload_hash'),
        'actor_id': actor_id,
        'decided_at': int(time.time_ns()),
    }
    try:
        output = await _execute_workflow(
            request,
            user,
            workflow,
            run_form,
            run_id=run_id,
            resume_state=consumed['state'],
            resume=resume,
            access_context_override=context,
        )
        return await Workflows.complete_run(run_id, 'success', output=output, db=db)
    except WorkflowPause as pause:
        return await _save_workflow_pause(run, workflow, context, pause, db=db)
    except Exception as exc:
        completed = await Workflows.complete_run(run_id, 'error', error=str(exc), db=db)
        raise HTTPException(status_code=400, detail=completed.error) from exc


async def resume_channel_workflow(
    request: Request,
    user,
    context: WorkflowAccessContext,
    workflow_id: str,
    run_id: str,
    decision: str,
    value: Any,
    revision: int,
    actor_id: str,
) -> WorkflowRunModel:
    if not context.channel_id or not context.company_user_id:
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.UNAUTHORIZED)
    workflow = await Workflows.get_by_id(workflow_id)
    if not workflow or workflow.status != 'published' or not workflow.default_version_id:
        raise HTTPException(status_code=409, detail='This workflow is no longer available.')
    if not workflow_channel_acl_allows(workflow, context):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.UNAUTHORIZED)
    run = await Workflows.get_run(run_id)
    if not run or run.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail='Workflow run not found.')
    return await _resume_workflow_run_internal(
        request,
        workflow,
        run,
        WorkflowResumeRequest(decision=decision, value=value, revision=revision),
        user,
        context,
        actor_id,
    )


@router.post('/{id}/runs/{run_id}/resume', response_model=WorkflowRunModel)
async def resume_workflow_run(
    request: Request,
    id: str,
    run_id: str,
    form_data: WorkflowResumeRequest,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    context = await _workflow_context_for_user(user, db)
    check_workflow_access(workflow, user, context)
    run = await Workflows.get_run(run_id, db=db)
    if not run or run.workflow_id != id:
        raise HTTPException(status_code=404, detail='Workflow run not found.')
    if run.user_id != user.id and user.role != 'admin':
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.UNAUTHORIZED)
    return await _resume_workflow_run_internal(
        request,
        workflow,
        run,
        form_data,
        user,
        context,
        user.id,
        db=db,
    )


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
    deleted = await Workflows.delete(id, db=db)
    if deleted:
        await _schedule_line_rich_menu_refresh(workflow)
    return {'success': deleted}
