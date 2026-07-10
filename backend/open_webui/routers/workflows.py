import hmac
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.workflows import (
    WorkflowForm,
    WorkflowListResponse,
    WorkflowModel,
    WorkflowPatchForm,
    WorkflowRunForm,
    WorkflowRunModel,
    WorkflowValidateResponse,
    WorkflowVersionModel,
    Workflows,
)
from open_webui.models.users import Users
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.workflows import execute_workflow_stub, validate_workflow_graph
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

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


def check_workflow_access(workflow: Optional[WorkflowModel], user, allow_public_template: bool = True):
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    can_read_public = allow_public_template and workflow.visibility == 'public_template'
    if user.role != 'admin' and user.id != workflow.user_id and not can_read_public:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)


def check_workflow_owner(workflow: Optional[WorkflowModel], user):
    if not workflow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    if user.role != 'admin' and user.id != workflow.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)


class ServiceCompanyWorkflowRequest(WorkflowForm):
    companyEmail: str


class ServiceCompanyRequest(WorkflowRunForm):
    companyEmail: str


class ServiceCompanyListRequest(WorkflowRunForm):
    companyEmail: str
    query: Optional[str] = None
    visibility: Optional[str] = None
    page: Optional[int] = 1


async def _resolve_service_user(company_email: str, db: AsyncSession):
    user = await Users.get_user_by_email(company_email.strip().lower(), db=db)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Company WebUI user not found.')
    return user


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

    return await Workflows.search(
        user_id=service_user.id,
        query=form_data.query,
        visibility=form_data.visibility,
        skip=(page - 1) * limit,
        limit=limit,
        include_public_templates=True,
        db=db,
    )


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
    validation = validate_workflow_graph(form_data.graph)
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    return await Workflows.insert(service_user.id, WorkflowForm(**form_data.model_dump(exclude={'companyEmail'})), db=db)


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

    validation = validate_workflow_graph(workflow.graph)
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
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, service_user)

    try:
        run = await Workflows.insert_run(id, service_user.id, form_data, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        output = await execute_workflow_stub(workflow, form_data.input)
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
    skip = (page - 1) * limit

    return await Workflows.search(
        user_id=user.id,
        query=query,
        visibility=visibility,
        skip=skip,
        limit=limit,
        include_public_templates=True,
        db=db,
    )


@router.post('/create', response_model=WorkflowModel)
async def create_new_workflow(
    request: Request,
    form_data: WorkflowForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    validation = validate_workflow_graph(form_data.graph)
    if not validation['ok']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    return await Workflows.insert(user.id, form_data, db=db)


@router.get('/{id}', response_model=WorkflowModel)
async def get_workflow_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user)
    return workflow


@router.post('/{id}/update', response_model=WorkflowModel)
async def update_workflow_by_id(
    request: Request,
    id: str,
    form_data: WorkflowPatchForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_owner(workflow, user)

    if form_data.graph is not None:
        validation = validate_workflow_graph(form_data.graph)
        if not validation['ok']:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation['errors'])

    return await Workflows.update_by_id(id, form_data, db=db)


@router.post('/{id}/validate', response_model=WorkflowValidateResponse)
async def validate_workflow_by_id(
    request: Request,
    id: str,
    form_data: Optional[WorkflowPatchForm] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await check_workflows_permission(user)
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user)

    graph = form_data.graph if form_data and form_data.graph is not None else workflow.graph
    return validate_workflow_graph(graph)


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

    validation = validate_workflow_graph(workflow.graph)
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
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user)
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
    workflow = await Workflows.get_by_id(id, db=db)
    check_workflow_access(workflow, user)

    try:
        run = await Workflows.insert_run(id, user.id, form_data, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        output = await execute_workflow_stub(workflow, form_data.input)
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
