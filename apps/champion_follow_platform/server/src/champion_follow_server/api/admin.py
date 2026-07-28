from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, or_, select

from champion_follow_server.api.dependencies import (
    AdminContext,
    require_admin_context,
    require_admin_csrf,
)
from champion_follow_server.db.session import get_session
from champion_follow_server.models.admin import AuditEvent, GlobalControl
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    CodePurpose,
    Device,
    DeviceStatus,
)
from champion_follow_server.models.device_tasks import DeviceTaskRevision
from champion_follow_server.models.signals import AnonymousActor, AsOfCandidate
from champion_follow_server.schemas.admin import (
    AuditItemResponse,
    AuditPage,
    AuthorizationCodeRequest,
    AuthorizationCodeResponse,
    ChampionItemResponse,
    ChampionPage,
    DeviceSummaryResponse,
    GlobalStopRequest,
    GlobalStopResponse,
    MutationStatusResponse,
    OverviewResponse,
    ReasonRequest,
    TaskItemResponse,
    TaskPage,
    ThresholdActivationRequest,
    ThresholdConfigResponse,
    ThresholdPreviewRequest,
    ThresholdPreviewResponse,
    ThresholdPreviewWindowResponse,
    UserDetailResponse,
    UserListResponse,
    UserReportResponse,
)
from champion_follow_server.schemas.device_tasks import CancelReason
from champion_follow_server.services.thresholds import (
    PreviewMismatch,
    ThresholdProposal,
)


router = APIRouter(prefix="/api/v1/admin", tags=["administrator"])
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
ACTOR_REF = re.compile(r"^A[0-9]{6,}$")
PUBLIC_DIRECTIONS = frozenset(
    {"BIG", "SMALL", "ODD", "EVEN", "PRIME", "COMPOSITE"}
)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _reason(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 500:
        raise HTTPException(status_code=422, detail="reason is required")
    return normalized


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    return supplied if REQUEST_ID.fullmatch(supplied) else str(uuid4())


def _encode_cursor(when: datetime, identity: UUID | int) -> str:
    payload = json.dumps(
        [when.isoformat(), str(identity)], separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, identity_type):
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
        when = datetime.fromisoformat(payload[0])
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError
        identity = identity_type(payload[1])
    except (ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc
    return when, identity


def _proposal(body: ThresholdPreviewRequest) -> ThresholdProposal:
    return ThresholdProposal(
        minimum_level=body.minimum_level,
        minimum_conservative_win_rate=body.minimum_conservative_win_rate,
        minimum_conservative_roi=body.minimum_conservative_roi,
        minimum_followable_rate=body.minimum_followable_rate,
    )


def _threshold_response(row) -> ThresholdConfigResponse:
    return ThresholdConfigResponse(
        config_id=row.id,
        config_version=row.config_version,
        scope=row.scope,
        device_id=row.device_id,
        minimum_level=row.minimum_level,
        minimum_conservative_win_rate=row.minimum_conservative_win_rate,
        minimum_conservative_roi=row.minimum_conservative_roi,
        minimum_followable_rate=row.minimum_followable_rate,
        effective_minimum_win_rate=row.effective_minimum_win_rate,
        is_removal=row.is_removal,
        activated_at=row.activated_at,
    )


def _preview_response(row) -> ThresholdPreviewResponse:
    return ThresholdPreviewResponse(
        preview_id=row.id,
        device_id=row.device_id,
        watermark_snapshot_id=row.watermark_snapshot_id,
        windows=[ThresholdPreviewWindowResponse.model_validate(item) for item in row.windows],
        expires_at=row.expires_at,
    )


def _publish_tasks(request: Request, tasks: list[DeviceTaskRevision]) -> None:
    for task in tasks:
        request.app.state.task_hub.publish(task.device_id, task.id)


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    response: Response,
    request: Request,
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    report = await request.app.state.report_service.admin_overview(
        db_session, now=request.app.state.clock.now()
    )
    control = await db_session.get(GlobalControl, "global-stop")
    return OverviewResponse(
        generated_at=report.generated_at,
        user_count=report.user_count,
        active_device_count=report.active_device_count,
        current_balance_minor=report.current_balance_minor,
        unrecognized_balance_adjustment_minor=(
            report.unrecognized_balance_adjustment_minor
        ),
        periods=report.periods,
        global_stop_enabled=bool(control and control.enabled),
        global_stop_version=control.version if control else None,
    )


@router.get("/users", response_model=UserListResponse)
async def users(
    response: Response,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    page = await request.app.state.report_service.list_users(
        db_session, limit=limit, cursor=cursor
    )
    return UserListResponse.model_validate(page)


@router.get("/users/{account_id}", response_model=UserDetailResponse)
async def user_detail(
    account_id: UUID,
    response: Response,
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    account = await db_session.scalar(
        select(Account).where(
            Account.id == account_id,
            Account.role == AccountRole.USER,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="user not found")
    devices = tuple(
        (
            await db_session.scalars(
                select(Device)
                .where(Device.account_id == account.id)
                .order_by(Device.created_at.desc(), Device.id.desc())
            )
        ).all()
    )
    return UserDetailResponse(
        account_id=account.id,
        username=account.username_canonical,
        status=account.status,
        created_at=account.created_at,
        devices=[
            DeviceSummaryResponse(
                device_id=device.id,
                status=device.status,
                binding_epoch=device.binding_epoch,
                created_at=device.created_at,
                updated_at=device.updated_at,
            )
            for device in devices
        ],
    )


@router.get("/users/{account_id}/report", response_model=UserReportResponse)
async def user_report(
    account_id: UUID,
    response: Response,
    request: Request,
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        report = await request.app.state.report_service.for_account(
            db_session,
            account_id=account_id,
            now=request.app.state.clock.now(),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="user not found") from None
    return UserReportResponse.model_validate(report)


@router.get("/champions", response_model=ChampionPage)
async def champions(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    statement = select(AsOfCandidate, AnonymousActor.display_no).join(
        AnonymousActor,
        and_(
            AnonymousActor.namespace_id == AsOfCandidate.namespace_id,
            AnonymousActor.actor_key == AsOfCandidate.actor_key,
        ),
    )
    if cursor is not None:
        cursor_time, cursor_id = _decode_cursor(cursor, UUID)
        statement = statement.where(
            or_(
                AsOfCandidate.frozen_at < cursor_time,
                and_(
                    AsOfCandidate.frozen_at == cursor_time,
                    AsOfCandidate.id < cursor_id,
                ),
            )
        )
    rows = list(
        (
            await db_session.execute(
                statement.order_by(
                    AsOfCandidate.frozen_at.desc(), AsOfCandidate.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = (
        _encode_cursor(page_rows[-1][0].frozen_at, page_rows[-1][0].id)
        if has_more and page_rows
        else None
    )
    return ChampionPage(
        items=[
            ChampionItemResponse(
                candidate_id=candidate.id,
                actor_ref=f"A{display_no:06d}",
                issue=candidate.issue,
                market=candidate.market,
                direction=candidate.direction.strip(),
                user_level=candidate.profile_level,
                sample_count=candidate.profile_sample_count,
                raw_win_rate=candidate.profile_raw_win_rate,
                conservative_win_rate=candidate.profile_conservative_win_rate,
                conservative_unit_return=(
                    candidate.profile_conservative_unit_return
                ),
                rank=candidate.base_rank,
                signal_state=(
                    "OPEN" if candidate.outcome is None else "SETTLED"
                ),
                frozen_at=candidate.frozen_at,
            )
            for candidate, display_no in page_rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/tasks", response_model=TaskPage)
async def tasks(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    statement = select(DeviceTaskRevision)
    if cursor is not None:
        cursor_time, cursor_id = _decode_cursor(cursor, UUID)
        statement = statement.where(
            or_(
                DeviceTaskRevision.created_at < cursor_time,
                and_(
                    DeviceTaskRevision.created_at == cursor_time,
                    DeviceTaskRevision.id < cursor_id,
                ),
            )
        )
    rows = list(
        (
            await db_session.scalars(
                statement.order_by(
                    DeviceTaskRevision.created_at.desc(),
                    DeviceTaskRevision.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = (
        _encode_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if has_more and page_rows
        else None
    )
    items = []
    for row in page_rows:
        actor_ref = row.payload.get("actor_ref")
        direction = row.payload.get("direction")
        ball = row.payload.get("ball")
        items.append(
            TaskItemResponse(
                task_id=row.id,
                device_id=row.device_id,
                period_id=row.period_id,
                revision=row.revision,
                action=row.action,
                actor_ref=(
                    actor_ref
                    if isinstance(actor_ref, str) and ACTOR_REF.fullmatch(actor_ref)
                    else None
                ),
                ball=ball if isinstance(ball, int) and 1 <= ball <= 5 else None,
                direction=(
                    direction
                    if isinstance(direction, str) and direction in PUBLIC_DIRECTIONS
                    else None
                ),
                issued_at=row.issued_at,
                expires_at=row.expires_at,
            )
        )
    return TaskPage(items=items, next_cursor=next_cursor)


@router.get("/audit", response_model=AuditPage)
async def audit(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _context: AdminContext = Depends(require_admin_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    statement = select(AuditEvent)
    if cursor is not None:
        cursor_time, cursor_id = _decode_cursor(cursor, int)
        statement = statement.where(
            or_(
                AuditEvent.created_at < cursor_time,
                and_(
                    AuditEvent.created_at == cursor_time,
                    AuditEvent.id < cursor_id,
                ),
            )
        )
    rows = list(
        (
            await db_session.scalars(
                statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = (
        _encode_cursor(page_rows[-1].created_at, page_rows[-1].id)
        if has_more and page_rows
        else None
    )
    return AuditPage(
        items=[
            AuditItemResponse(
                audit_id=row.id,
                actor_account_id=row.actor_account_id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                old_state=row.old_state,
                new_state=row.new_state,
                reason=row.reason,
                request_id=row.request_id,
                created_at=row.created_at,
            )
            for row in page_rows
        ],
        next_cursor=next_cursor,
    )


@router.post(
    "/authorization-codes",
    response_model=AuthorizationCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def authorization_code(
    body: AuthorizationCodeRequest,
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    reason = _reason(body.reason)
    purpose = CodePurpose(body.purpose)
    if (purpose == CodePurpose.REGISTER) != (body.target_account_id is None):
        raise HTTPException(status_code=422, detail="invalid authorization code target")
    if body.target_account_id is not None:
        target = await db_session.scalar(
            select(Account).where(
                Account.id == body.target_account_id,
                Account.role == AccountRole.USER,
            )
        )
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
    issued = await request.app.state.authorization_code_service.issue(
        db_session,
        actor=context.account,
        purpose=purpose,
        target_account_id=body.target_account_id,
        reason=reason,
        request_id=_request_id(request),
    )
    await db_session.commit()
    return AuthorizationCodeResponse(
        authorization_code=issued.plaintext,
        purpose=issued.row.purpose,
        target_account_id=issued.row.target_account_id,
        expires_at=issued.row.expires_at,
    )


@router.post("/devices/{device_id}/unbind", response_model=MutationStatusResponse)
async def unbind_device(
    device_id: UUID,
    body: ReasonRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    reason = _reason(body.reason)
    device = await db_session.scalar(
        select(Device).where(Device.id == device_id).with_for_update()
    )
    if device is None or device.status != DeviceStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="active device not found")
    tasks = await request.app.state.task_revision_service.cancel_live_bets(
        db_session,
        reason=CancelReason.DEVICE_UNBOUND,
        device_ids={device.id},
    )
    old_state = {
        "status": device.status.value,
        "binding_epoch": device.binding_epoch,
    }
    device.status = DeviceStatus.UNBOUND
    device.unbound_at = request.app.state.clock.now()
    device.binding_epoch += 1
    await request.app.state.session_service.revoke_device(db_session, device.id)
    await request.app.state.audit_writer.append(
        db_session,
        actor_account_id=context.account.id,
        action="DEVICE_UNBOUND",
        target_type="device",
        target_id=str(device.id),
        old_state=old_state,
        new_state={
            "status": device.status.value,
            "binding_epoch": device.binding_epoch,
        },
        reason=reason,
        request_id=_request_id(request),
    )
    await db_session.commit()
    _publish_tasks(request, tasks)
    return MutationStatusResponse(status="ok", cancelled_task_count=len(tasks))


@router.post("/accounts/{account_id}/disable", response_model=MutationStatusResponse)
async def disable_account(
    account_id: UUID,
    body: ReasonRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    reason = _reason(body.reason)
    account = await db_session.scalar(
        select(Account)
        .where(Account.id == account_id, Account.role == AccountRole.USER)
        .with_for_update()
    )
    if account is None or account.status == AccountStatus.DISABLED:
        raise HTTPException(status_code=404, detail="active user not found")
    device_ids = set(
        (
            await db_session.scalars(
                select(Device.id).where(Device.account_id == account.id)
            )
        ).all()
    )
    tasks = await request.app.state.task_revision_service.cancel_live_bets(
        db_session,
        reason=CancelReason.ACCOUNT_DISABLED,
        device_ids=device_ids,
    )
    old_status = account.status
    account.status = AccountStatus.DISABLED
    await request.app.state.session_service.revoke_account(db_session, account.id)
    await request.app.state.audit_writer.append(
        db_session,
        actor_account_id=context.account.id,
        action="ACCOUNT_DISABLED",
        target_type="account",
        target_id=str(account.id),
        old_state={"status": old_status.value},
        new_state={"status": account.status.value},
        reason=reason,
        request_id=_request_id(request),
    )
    await db_session.commit()
    _publish_tasks(request, tasks)
    return MutationStatusResponse(status="ok", cancelled_task_count=len(tasks))


@router.post("/thresholds/preview", response_model=ThresholdPreviewResponse)
async def preview_threshold(
    body: ThresholdPreviewRequest,
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    row = await request.app.state.threshold_service.preview(
        db_session,
        actor=context.account,
        proposal=_proposal(body),
        device_id=body.device_id,
        now=request.app.state.clock.now(),
    )
    await db_session.commit()
    return _preview_response(row)


@router.post("/thresholds", response_model=ThresholdConfigResponse)
async def activate_threshold(
    body: ThresholdActivationRequest,
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    reason = _reason(body.reason)
    try:
        row = await request.app.state.threshold_service.activate(
            db_session,
            actor=context.account,
            proposal=_proposal(body),
            device_id=body.device_id,
            preview_id=body.preview_id,
            reason=reason,
            request_id=_request_id(request),
            now=request.app.state.clock.now(),
        )
    except PreviewMismatch:
        await db_session.rollback()
        raise HTTPException(status_code=409, detail="matching preview required") from None
    tasks = await request.app.state.task_revision_service.cancel_live_bets(
        db_session,
        reason=CancelReason.THRESHOLD_CHANGED,
        device_ids={body.device_id} if body.device_id else None,
    )
    await db_session.commit()
    _publish_tasks(request, tasks)
    return _threshold_response(row)


@router.delete(
    "/devices/{device_id}/threshold-override",
    response_model=ThresholdConfigResponse,
)
async def remove_threshold_override(
    device_id: UUID,
    body: ReasonRequest,
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    row = await request.app.state.threshold_service.remove_override(
        db_session,
        actor=context.account,
        device_id=device_id,
        reason=_reason(body.reason),
        request_id=_request_id(request),
        now=request.app.state.clock.now(),
    )
    tasks = await request.app.state.task_revision_service.cancel_live_bets(
        db_session,
        reason=CancelReason.THRESHOLD_CHANGED,
        device_ids={device_id},
    )
    await db_session.commit()
    _publish_tasks(request, tasks)
    return _threshold_response(row)


@router.post("/global-stop", response_model=GlobalStopResponse)
async def set_global_stop(
    body: GlobalStopRequest,
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    reason = _reason(body.reason)
    control = await db_session.scalar(
        select(GlobalControl)
        .where(GlobalControl.key == "global-stop")
        .with_for_update()
    )
    old_state = (
        {"enabled": control.enabled, "version": control.version}
        if control is not None
        else None
    )
    now = request.app.state.clock.now()
    if control is None:
        control = GlobalControl(
            key="global-stop",
            enabled=body.enabled,
            version=1,
            reason=reason,
            updated_by_account_id=context.account.id,
            updated_at=now,
        )
        db_session.add(control)
    else:
        control.enabled = body.enabled
        control.version += 1
        control.reason = reason
        control.updated_by_account_id = context.account.id
        control.updated_at = now
    await db_session.flush()
    tasks = []
    if body.enabled:
        tasks = await request.app.state.task_revision_service.cancel_live_bets(
            db_session, reason=CancelReason.GLOBAL_STOP
        )
    await request.app.state.audit_writer.append(
        db_session,
        actor_account_id=context.account.id,
        action="GLOBAL_STOP_UPDATED",
        target_type="global_control",
        target_id="global-stop",
        old_state=old_state,
        new_state={"enabled": control.enabled, "version": control.version},
        reason=reason,
        request_id=_request_id(request),
    )
    await db_session.commit()
    _publish_tasks(request, tasks)
    return GlobalStopResponse(
        enabled=control.enabled,
        version=control.version,
        reason=control.reason,
        updated_at=control.updated_at,
    )
