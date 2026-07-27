import argparse
import asyncio
import getpass
import os
from pathlib import Path

import qrcode

from champion_follow_server.config import Settings
from champion_follow_server.db.session import open_auth_engine
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretVault
from champion_follow_server.services.admin_bootstrap import AdminBootstrapService
from champion_follow_server.services.audit import AuditWriter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="champion-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="initialize the sole administrator"
    )
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--qr-output", required=True, type=Path)
    return parser


async def _bootstrap(args: argparse.Namespace) -> int:
    settings = Settings()
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("password confirmation does not match")

    vault = SecretVault(settings.secret_vault_key_path.read_bytes())
    service = AdminBootstrapService(PasswordHasher(), vault)
    qr_path: Path = args.qr_output.resolve()
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with open_auth_engine(settings.database_url) as auth:
            async with auth.session_factory() as session:
                pending = await service.create_pending_admin(
                    session,
                    username=args.username,
                    password=password,
                    issuer="Champion Follow",
                )
                del password, confirmation
                provisioning_uri = pending.provisioning_uri
                image = qrcode.make(provisioning_uri)
                del provisioning_uri
                image.save(qr_path)
                os.chmod(qr_path, 0o600)
                print(qr_path)
                code = getpass.getpass("Current six-digit code: ")
                await service.confirm_totp(session, pending.account_id, code)
                await AuditWriter().append(
                    session,
                    actor_account_id=pending.account_id,
                    action="ADMIN_BOOTSTRAPPED",
                    target_type="account",
                    target_id=str(pending.account_id),
                    old_state=None,
                    new_state={"username": args.username.strip().casefold()},
                    reason="initial sole administrator bootstrap",
                    request_id="local-admin-bootstrap",
                )
                await session.commit()
        return 0
    finally:
        qr_path.unlink(missing_ok=True)


def main() -> int:
    args = _parser().parse_args()
    if args.command == "bootstrap":
        try:
            return asyncio.run(_bootstrap(args))
        except (OSError, RuntimeError, ValueError):
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
