from pathlib import Path

from champion_follow_server.schemas.admin import (
    AuditPage,
    AuthorizationCodeResponse,
    ChampionPage,
    OverviewResponse,
    TaskPage,
    ThresholdConfigResponse,
    ThresholdPreviewResponse,
    UserDetailResponse,
    UserListResponse,
    UserReportResponse,
)
from champion_follow_server.schemas.auth import (
    AdminSessionResponse,
    EnrollmentResponse,
    UserSessionResponse,
)


FORBIDDEN_KEYS = {
    "password_hash",
    "access_digest",
    "refresh_digest",
    "csrf_digest",
    "secret_ciphertext",
    "public_key_spki_der",
    "actor_key",
    "platform_order_ref",
    "platform_cookie",
    "platform_token",
    "private_key",
}


def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_every_documented_api_response_schema_excludes_private_fields() -> None:
    response_models = (
        AdminSessionResponse,
        EnrollmentResponse,
        UserSessionResponse,
        AuditPage,
        AuthorizationCodeResponse,
        ChampionPage,
        OverviewResponse,
        TaskPage,
        ThresholdConfigResponse,
        ThresholdPreviewResponse,
        UserDetailResponse,
        UserListResponse,
        UserReportResponse,
    )
    seen = {
        key
        for model in response_models
        for key in walk(model.model_json_schema(mode="serialization"))
    }

    assert seen.isdisjoint(FORBIDDEN_KEYS)


def test_admin_browser_assets_do_not_persist_session_material() -> None:
    static = Path(__file__).parents[1] / "static" / "admin"
    script = (static / "app.js").read_text(encoding="utf-8")

    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "indexedDB" not in script
    assert "document.cookie" not in script
    assert "innerHTML" not in script
    assert "outerHTML" not in script
