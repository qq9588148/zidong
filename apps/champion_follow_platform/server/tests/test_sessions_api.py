import pytest
import pyotp

from champion_follow_server.models.auth import DeviceStatus


@pytest.mark.asyncio
async def test_user_login_requires_password_and_bound_device_proof(
    client, active_device, device_login_proof
) -> None:
    response = await client.post(
        "/api/v1/auth/device/login", json=device_login_proof
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "access_token",
        "refresh_token",
        "access_expires_at",
        "device_id",
    }
    assert body["device_id"] == str(active_device.id)
    assert response.headers["cache-control"] == "no-store"
    keys = await client.get(
        "/api/v1/auth/task-signing-keys",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert keys.status_code == 200
    assert set(keys.json()) == {"keys"}
    assert set(keys.json()["keys"][0]) == {
        "version",
        "public_key_spki_der_b64",
        "sha256",
    }


@pytest.mark.asyncio
async def test_admin_login_requires_totp_and_uses_httponly_refresh_cookie(
    client, confirmed_admin, current_admin_otp
) -> None:
    response = await client.post(
        "/api/v1/admin/session",
        json={
            "username": "owner",
            "password": "test-admin-password-with-16-chars",
            "totp": current_admin_otp,
        },
        headers={"Origin": "https://console.example.test"},
    )
    assert response.status_code == 200
    assert set(response.json()) == {
        "access_token",
        "access_expires_at",
        "csrf_token",
    }
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


@pytest.mark.asyncio
async def test_disabled_account_session_is_rejected(
    client, disabled_user_access_token
) -> None:
    response = await client.get(
        "/api/v1/me/report",
        headers={"Authorization": f"Bearer {disabled_user_access_token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_revoke_the_whole_family(
    client, device_login_proof
) -> None:
    login = await client.post(
        "/api/v1/auth/device/login", json=device_login_proof
    )
    first = login.json()
    rotated_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()

    reused = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert reused.status_code == 401
    after_reuse = await client.get(
        "/api/v1/auth/task-signing-keys",
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    )
    assert after_reuse.status_code == 401


@pytest.mark.asyncio
async def test_device_unbind_invalidates_access_immediately(
    client,
    active_device,
    device_login_proof,
    auth_session_factory,
    clock,
) -> None:
    login = await client.post(
        "/api/v1/auth/device/login", json=device_login_proof
    )
    token = login.json()["access_token"]
    async with auth_session_factory() as session:
        device = await session.get(type(active_device), active_device.id)
        device.status = DeviceStatus.UNBOUND
        device.unbound_at = clock.now()
        await session.commit()

    response = await client.get(
        "/api/v1/auth/task-signing-keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_five_bad_admin_logins_lock_for_fifteen_minutes(
    client, confirmed_admin, clock
) -> None:
    _account, seed = confirmed_admin
    origin = {"Origin": "https://console.example.test"}
    for _ in range(5):
        response = await client.post(
            "/api/v1/admin/session",
            json={
                "username": "owner",
                "password": "wrong-test-password-with-16-chars",
                "totp": pyotp.TOTP(seed).at(clock.now()),
            },
            headers=origin,
        )
        assert response.status_code == 401

    locked = await client.post(
        "/api/v1/admin/session",
        json={
            "username": "owner",
            "password": "test-admin-password-with-16-chars",
            "totp": pyotp.TOTP(seed).at(clock.now()),
        },
        headers=origin,
    )
    assert locked.status_code == 401

    clock.advance(minutes=16)
    unlocked = await client.post(
        "/api/v1/admin/session",
        json={
            "username": "owner",
            "password": "test-admin-password-with-16-chars",
            "totp": pyotp.TOTP(seed).at(clock.now()),
        },
        headers=origin,
    )
    assert unlocked.status_code == 200
