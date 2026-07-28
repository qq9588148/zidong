import pytest


@pytest.mark.asyncio
async def test_admin_console_has_required_panels_and_security_headers(client) -> None:
    response = await client.get("/admin/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self' wss:"
    )
    html = response.text
    for marker in (
        'id="login-panel"',
        'id="overview-panel"',
        'id="users-panel"',
        'id="champions-panel"',
        'id="threshold-panel"',
        'id="platform-panel"',
        'id="authorization-panel"',
        'id="audit-panel"',
        'id="global-stop"',
        'id="authorization-code-dialog"',
    ):
        assert marker in html


@pytest.mark.asyncio
async def test_admin_assets_do_not_persist_tokens_or_render_unsafe_html(client) -> None:
    script_response = await client.get("/admin/app.js")
    style_response = await client.get("/admin/style.css")

    assert script_response.status_code == 200
    assert style_response.status_code == 200
    script = script_response.text
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "innerHTML",
        "outerHTML",
        "document.write",
        "eval(",
    ):
        assert forbidden not in script
    assert "textContent" in script
    assert 'credentials: "same-origin"' in script
    assert 'cache: "no-store"' in script
    assert "accessToken" in script and "csrfToken" in script


@pytest.mark.asyncio
async def test_admin_static_routes_do_not_expose_directory_listing(client) -> None:
    response = await client.get("/admin/not-a-real-asset")

    assert response.status_code == 404
