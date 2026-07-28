import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)

from champion_follow_server.api.dependencies import (
    AdminContext,
    DeviceContext,
    UserContext,
    require_active_device_context,
    require_admin_csrf,
    require_user_context,
)
from champion_follow_server.db.session import get_session
from champion_follow_server.schemas.auth import (
    AdminLoginRequest,
    AdminSessionResponse,
    DeviceLoginChallengeRequest,
    DeviceLoginChallengeResponse,
    DeviceLoginRequest,
    EnrollmentChallengeRequest,
    EnrollmentChallengeResponse,
    EnrollmentResponse,
    RebindRequest,
    RegistrationRequest,
    TaskSigningKeyResponse,
    TaskSigningKeysResponse,
    UserRefreshRequest,
    UserSessionResponse,
)
from champion_follow_server.schemas.admin import UserReportResponse
from champion_follow_server.services.device_binding import InvalidEnrollment
from champion_follow_server.services.sessions import AuthenticationFailed


router = APIRouter()
ADMIN_REFRESH_COOKIE = "__Host-champion_admin_refresh"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _require_trusted_origin(request: Request) -> None:
    expected = str(request.app.state.settings.trusted_admin_origin).rstrip("/")
    if request.headers.get("origin", "").rstrip("/") != expected:
        raise HTTPException(status_code=403, detail="request rejected")


def _set_admin_cookie(response: Response, value: str, max_age: int) -> None:
    response.set_cookie(
        key=ADMIN_REFRESH_COOKIE,
        value=value,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


@router.post(
    "/api/v1/enrollment/challenge",
    response_model=EnrollmentChallengeResponse,
)
async def enrollment_challenge(
    body: EnrollmentChallengeRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        result = await request.app.state.binding_service.create_challenge(
            db_session, body.authorization_code.get_secret_value()
        )
        await db_session.commit()
    except InvalidEnrollment:
        await db_session.rollback()
        raise HTTPException(status_code=400, detail="enrollment unavailable") from None
    return EnrollmentChallengeResponse(
        challenge_id=result.id, nonce=result.nonce_b64
    )


@router.post(
    "/api/v1/enrollment/register", response_model=EnrollmentResponse
)
async def enrollment_register(
    body: RegistrationRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        result = await request.app.state.binding_service.register(
            db_session,
            code_plaintext=body.authorization_code.get_secret_value(),
            challenge_id=body.challenge_id,
            username=body.username,
            password=body.password.get_secret_value(),
            public_key_spki_der_b64=body.public_key_spki_der,
            proof_der_b64=body.proof_der,
        )
        await db_session.commit()
    except InvalidEnrollment:
        await db_session.rollback()
        raise HTTPException(status_code=400, detail="enrollment unavailable") from None
    return EnrollmentResponse(
        account_id=result.account.id,
        device_id=result.device.id,
        public_key_fingerprint=result.device.public_key_fingerprint.hex(),
    )


@router.post("/api/v1/enrollment/rebind", response_model=EnrollmentResponse)
async def enrollment_rebind(
    body: RebindRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        result = await request.app.state.binding_service.rebind(
            db_session,
            code_plaintext=body.authorization_code.get_secret_value(),
            challenge_id=body.challenge_id,
            username=body.username,
            password=body.password.get_secret_value(),
            public_key_spki_der_b64=body.public_key_spki_der,
            proof_der_b64=body.proof_der,
        )
        await db_session.commit()
    except InvalidEnrollment:
        await db_session.rollback()
        raise HTTPException(status_code=400, detail="enrollment unavailable") from None
    return EnrollmentResponse(
        account_id=result.account.id,
        device_id=result.device.id,
        public_key_fingerprint=result.device.public_key_fingerprint.hex(),
    )


@router.post(
    "/api/v1/auth/device/challenge",
    response_model=DeviceLoginChallengeResponse,
)
async def device_login_challenge(
    body: DeviceLoginChallengeRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        challenge = (
            await request.app.state.session_service.create_device_challenge(
                db_session, body.username
            )
        )
        await db_session.commit()
    except AuthenticationFailed:
        await db_session.rollback()
        raise HTTPException(status_code=401, detail="authentication required") from None
    return DeviceLoginChallengeResponse(
        challenge_id=challenge.id,
        nonce=base64.b64encode(challenge.nonce).decode("ascii"),
    )


@router.post(
    "/api/v1/auth/device/login", response_model=UserSessionResponse
)
async def device_login(
    body: DeviceLoginRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        pair = await request.app.state.session_service.login_device(
            db_session,
            challenge_id=body.challenge_id,
            username=body.username,
            password=body.password.get_secret_value(),
            proof_der_b64=body.proof_der,
        )
        await db_session.commit()
    except AuthenticationFailed:
        await db_session.commit()
        raise HTTPException(status_code=401, detail="authentication required") from None
    return UserSessionResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        device_id=pair.auth_session.device_id,
    )


@router.post("/api/v1/auth/refresh", response_model=UserSessionResponse)
async def user_refresh(
    body: UserRefreshRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    try:
        pair = await request.app.state.session_service.rotate_refresh(
            db_session, body.refresh_token.get_secret_value()
        )
        if pair.auth_session.device_id is None:
            raise AuthenticationFailed("authentication required")
        await db_session.commit()
    except AuthenticationFailed:
        await db_session.commit()
        raise HTTPException(status_code=401, detail="authentication required") from None
    return UserSessionResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        device_id=pair.auth_session.device_id,
    )


@router.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def user_logout(
    response: Response,
    request: Request,
    context: UserContext = Depends(require_user_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    await request.app.state.session_service.revoke_session(
        db_session, context.auth_session
    )
    await db_session.commit()


@router.get(
    "/api/v1/auth/task-signing-keys",
    response_model=TaskSigningKeysResponse,
)
async def task_signing_keys(
    response: Response,
    request: Request,
    _context: DeviceContext = Depends(require_active_device_context),
):
    _no_store(response)
    signer = request.app.state.task_signer
    spki = signer.public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return TaskSigningKeysResponse(
        keys=[
            TaskSigningKeyResponse(
                version=signer.key_version,
                public_key_spki_der_b64=base64.b64encode(spki).decode("ascii"),
                sha256=hashlib.sha256(spki).hexdigest(),
            )
        ]
    )


@router.post("/api/v1/admin/session", response_model=AdminSessionResponse)
async def admin_login(
    body: AdminLoginRequest,
    response: Response,
    request: Request,
    db_session=Depends(get_session),
):
    _no_store(response)
    _require_trusted_origin(request)
    try:
        pair = await request.app.state.session_service.login_admin(
            db_session,
            username=body.username,
            password=body.password.get_secret_value(),
        )
        await db_session.commit()
    except AuthenticationFailed:
        await db_session.commit()
        raise HTTPException(status_code=401, detail="authentication required") from None
    _set_admin_cookie(
        response,
        pair.refresh_token,
        request.app.state.settings.refresh_token_ttl_seconds,
    )
    return AdminSessionResponse(
        access_token=pair.access_token,
        access_expires_at=pair.access_expires_at,
        csrf_token=pair.csrf_token,
    )


@router.post(
    "/api/v1/admin/session/refresh", response_model=AdminSessionResponse
)
async def admin_refresh(
    response: Response,
    request: Request,
    db_session=Depends(get_session),
    refresh_token: str | None = Cookie(
        default=None, alias=ADMIN_REFRESH_COOKIE
    ),
    csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    _no_store(response)
    _require_trusted_origin(request)
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        pair = await request.app.state.session_service.rotate_refresh(
            db_session, refresh_token, csrf_token=csrf_token
        )
        if pair.csrf_token is None:
            raise AuthenticationFailed("authentication required")
        await db_session.commit()
    except AuthenticationFailed:
        await db_session.commit()
        raise HTTPException(status_code=401, detail="authentication required") from None
    _set_admin_cookie(
        response,
        pair.refresh_token,
        request.app.state.settings.refresh_token_ttl_seconds,
    )
    return AdminSessionResponse(
        access_token=pair.access_token,
        access_expires_at=pair.access_expires_at,
        csrf_token=pair.csrf_token,
    )


@router.delete("/api/v1/admin/session", status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout(
    response: Response,
    request: Request,
    context: AdminContext = Depends(require_admin_csrf),
    db_session=Depends(get_session),
):
    _no_store(response)
    await request.app.state.session_service.revoke_session(
        db_session, context.auth_session
    )
    await db_session.commit()
    response.delete_cookie(
        ADMIN_REFRESH_COOKIE,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


@router.get("/api/v1/me/report", response_model=UserReportResponse)
async def own_report(
    response: Response,
    request: Request,
    context: UserContext = Depends(require_user_context),
    db_session=Depends(get_session),
):
    _no_store(response)
    report = await request.app.state.report_service.for_account(
        db_session,
        account_id=context.account.id,
        now=request.app.state.clock.now(),
    )
    return UserReportResponse.model_validate(report)
