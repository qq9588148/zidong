from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from champion_follow_server.db.session import get_session
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AuthSession,
    Device,
)


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class UserContext:
    account: Account
    auth_session: AuthSession


@dataclass(frozen=True, slots=True)
class DeviceContext(UserContext):
    device: Device


@dataclass(frozen=True, slots=True)
class AdminContext(UserContext):
    pass


async def _context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db_session,
) -> UserContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="authentication required")
    result = await request.app.state.session_service.authenticate_access(
        db_session, credentials.credentials
    )
    if result is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return UserContext(result.account, result.auth_session)


async def require_user_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> UserContext:
    return await _context(request, credentials, db_session)


async def require_active_device_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> DeviceContext:
    context = await _context(request, credentials, db_session)
    device = await request.app.state.session_service.active_device_for(
        db_session, context.auth_session
    )
    if device is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return DeviceContext(context.account, context.auth_session, device)


async def require_admin_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> AdminContext:
    context = await _context(request, credentials, db_session)
    if context.account.role is not AccountRole.ADMIN:
        raise HTTPException(status_code=403, detail="administrator required")
    return AdminContext(context.account, context.auth_session)


async def require_admin_csrf(
    request: Request,
    context: AdminContext = Depends(require_admin_context),
) -> AdminContext:
    expected_origin = str(
        request.app.state.settings.trusted_admin_origin
    ).rstrip("/")
    if request.headers.get("origin", "").rstrip("/") != expected_origin:
        raise HTTPException(status_code=403, detail="request rejected")
    supplied = request.headers.get("x-csrf-token", "")
    if not request.app.state.session_service.verify_csrf(
        context.auth_session, supplied
    ):
        raise HTTPException(status_code=403, detail="request rejected")
    return context
