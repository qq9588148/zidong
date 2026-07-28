import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select

from champion_follow_server.models.admin import PlatformEndpointConfig


def normalize_platform_entry_url(value: str) -> tuple[str, list[str]]:
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        normalized = urlunsplit(
            ("https", parsed.netloc, parsed.path or "/", "", "")
        )
        origin = urlunsplit(("https", parsed.netloc, "", "", ""))
        if len(normalized) > 2048 or len(origin) > 256:
            raise ValueError
        return normalized, [origin]
    except (TypeError, ValueError):
        raise ValueError("invalid platform endpoint") from None


def utc_millis(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def public_envelope(row: PlatformEndpointConfig) -> dict:
    return {
        "config_version": row.config_version,
        "issued_at": utc_millis(row.issued_at),
        "expires_at": utc_millis(row.expires_at),
        "entry_url": row.entry_url,
        "allowed_origins": list(row.allowed_origins),
        "signing_key_version": row.signing_key_version,
        "signature": base64.urlsafe_b64encode(row.signature).decode("ascii"),
    }


class PlatformEndpointService:
    def __init__(self, signer, audit_writer, clock) -> None:
        self._signer = signer
        self._audit = audit_writer
        self._clock = clock

    async def current(self, session) -> PlatformEndpointConfig | None:
        return await session.get(PlatformEndpointConfig, "default")

    async def update(
        self,
        session,
        *,
        actor_account_id: UUID,
        entry_url: str,
        reason: str,
        request_id: str,
    ) -> PlatformEndpointConfig:
        normalized, origins = normalize_platform_entry_url(entry_url)
        current = await session.scalar(
            select(PlatformEndpointConfig)
            .where(PlatformEndpointConfig.key == "default")
            .with_for_update()
        )
        now = self._clock.now()
        expires = now + timedelta(days=30)
        # Version 1 is reserved for the desktop's built-in bootstrap endpoint.
        version = 2 if current is None else current.config_version + 1
        unsigned = {
            "config_version": version,
            "issued_at": utc_millis(now),
            "expires_at": utc_millis(expires),
            "entry_url": normalized,
            "allowed_origins": origins,
            "signing_key_version": self._signer.key_version,
        }
        signature = self._signer.sign(unsigned)
        old_state = None if current is None else {
            "config_version": current.config_version,
            "entry_url": current.entry_url,
            "allowed_origins": list(current.allowed_origins),
        }
        if current is None:
            current = PlatformEndpointConfig(key="default")
            session.add(current)
        current.config_version = version
        current.entry_url = normalized
        current.allowed_origins = origins
        current.issued_at = now
        current.expires_at = expires
        current.signing_key_version = self._signer.key_version
        current.signature = signature
        current.reason = reason
        current.updated_by_account_id = actor_account_id
        current.updated_at = now
        await session.flush()
        await self._audit.append(
            session,
            actor_account_id=actor_account_id,
            action="PLATFORM_ENDPOINT_CHANGED",
            target_type="platform_endpoint",
            target_id="default",
            old_state=old_state,
            new_state={
                "config_version": version,
                "entry_url": normalized,
                "allowed_origins": origins,
            },
            reason=reason,
            request_id=request_id,
        )
        return current
