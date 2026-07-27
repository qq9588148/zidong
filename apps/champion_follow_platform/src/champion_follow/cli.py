import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .contracts.events import VERSION
from .db import create_pool
from .migrations import migrate
from .repositories.issues import IssueRepository
from .services.causal import CausalProcessor
from .services.history_import import import_legacy
from .services.issue_builder import IssueBuilder


def _restrict_handoff_permissions(descriptor, path):
    if os.name != "nt":
        os.fchmod(descriptor, 0o600)
        return

    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    if not username:
        raise OSError("unable to secure credential handoff")
    identity = f"{domain}\\{username}" if domain else username
    completed = subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:(R,W)",
            "/q",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError("unable to secure credential handoff")


async def _initialize_namespace(settings, version):
    if VERSION.fullmatch(version) is None:
        raise ValueError("invalid_namespace_version")

    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        namespace_id = uuid4()
        async with pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO identity_namespaces(id,version,mode) "
                    "VALUES (%s,%s,'active')",
                    (namespace_id, version),
                )
        return {"status": "created", "version": version}
    finally:
        await pool.close()


async def _migrate_only(settings):
    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        return {"status": "migrated"}
    finally:
        await pool.close()


async def _register_collector(
    settings,
    label,
    wire_id,
    namespace_version,
    parser_version,
    handoff_path,
):
    if VERSION.fullmatch(namespace_version) is None:
        raise ValueError("invalid_namespace_version")
    if VERSION.fullmatch(parser_version) is None:
        raise ValueError("invalid_parser_version")

    bearer = secrets.token_urlsafe(48)
    bearer_sha256 = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    bundle = {
        "format": "champion-collector-credential-v1",
        "collector_id": wire_id,
        "bearer": bearer,
    }
    path = Path(handoff_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    created = False
    pool = None
    committed = False
    handoff_durable = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        _restrict_handoff_permissions(descriptor, path)
        pool = create_pool(settings.database_url.get_secret_value())
        await pool.open(wait=True)
        await migrate(pool)
        async with pool.connection() as connection:
            async with connection.transaction():
                namespace = await (
                    await connection.execute(
                        "SELECT id FROM identity_namespaces "
                        "WHERE version=%s AND mode='active' FOR UPDATE",
                        (namespace_version,),
                    )
                ).fetchone()
                if namespace is None:
                    raise ValueError("namespace_not_found")
                anchor = await (
                    await connection.execute(
                        "SELECT event_key AS history_anchor_event_key FROM source_events "
                        "WHERE namespace_id=%s AND partition='current' "
                        "AND kind IN ('bet','cancel') "
                        "ORDER BY source_ms DESC,event_key DESC LIMIT 1",
                        (namespace["id"],),
                    )
                ).fetchone()
                await connection.execute(
                    "INSERT INTO collectors(id,namespace_id,wire_id,label,parser_version,"
                    "bearer_sha256,history_anchor_event_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        namespace["id"],
                        wire_id,
                        label,
                        parser_version,
                        bearer_sha256,
                        anchor["history_anchor_event_key"] if anchor else None,
                    ),
                )
                stream = os.fdopen(descriptor, "w", encoding="utf-8")
                descriptor = -1
                with stream:
                    stream.write(
                        json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                handoff_durable = True
        committed = True
        return {
            "status": "created",
            "label": label,
            "collector_id": wire_id,
            "credential_handoff": str(path),
        }
    finally:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        finally:
            try:
                if pool is not None:
                    await pool.close()
            finally:
                if created and not committed and not handoff_durable:
                    path.unlink(missing_ok=True)


async def _import(settings, args):
    if VERSION.fullmatch(args.namespace_version) is None:
        raise ValueError("invalid_namespace_version")
    if VERSION.fullmatch(args.parser_version) is None:
        raise ValueError("invalid_parser_version")

    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        result = await import_legacy(
            pool,
            args.source,
            args.source_label,
            args.namespace_version,
            args.partition,
            args.parser_version,
        )
        return {
            "status": result.status,
            "inserted": result.inserted,
            "partition": result.partition,
            "row_count": result.row_count,
        }
    finally:
        await pool.close()


async def _process_ready(settings, namespace_version):
    if VERSION.fullmatch(namespace_version) is None:
        raise ValueError("invalid_namespace_version")

    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open(wait=True)
    try:
        await migrate(pool)
        async with pool.connection() as connection:
            namespace = await (
                await connection.execute(
                    "SELECT id FROM identity_namespaces "
                    "WHERE version=%s AND mode='active'",
                    (namespace_version,),
                )
            ).fetchone()
        if namespace is None:
            raise ValueError("namespace_not_found")

        evaluations = await IssueBuilder(IssueRepository(pool)).build_pending(
            namespace["id"]
        )
        outcomes = await CausalProcessor(pool).process_ready(
            namespace_version=namespace_version
        )
        return {
            "status": "processed",
            "evaluated": len(evaluations),
            "processed": outcomes.count("processed"),
            "excluded": outcomes.count("excluded"),
            "already_processed": outcomes.count("already_processed"),
        }
    finally:
        await pool.close()


def _run(coroutine):
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coroutine)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="champion-follow")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    initialize = commands.add_parser("init-namespace")
    initialize.add_argument("--version", required=True)
    register = commands.add_parser("register-collector")
    register.add_argument("--label", required=True)
    register.add_argument("--collector-id", required=True)
    register.add_argument("--namespace-version", required=True)
    register.add_argument("--parser-version", required=True)
    register.add_argument("--credential-handoff", required=True)
    imported = commands.add_parser("import-legacy")
    imported.add_argument("--source", required=True)
    imported.add_argument("--source-label", required=True)
    imported.add_argument("--namespace-version", required=True)
    imported.add_argument(
        "--partition",
        choices=("current", "baseline"),
        required=True,
    )
    imported.add_argument("--parser-version", required=True)
    process = commands.add_parser("process-ready")
    process.add_argument("--namespace-version", required=True)
    args = parser.parse_args(argv)
    settings = Settings()
    if args.command == "migrate":
        result = _run(_migrate_only(settings))
    elif args.command == "init-namespace":
        result = _run(_initialize_namespace(settings, args.version))
    elif args.command == "register-collector":
        result = _run(
            _register_collector(
                settings,
                args.label,
                args.collector_id,
                args.namespace_version,
                args.parser_version,
                args.credential_handoff,
            )
        )
    elif args.command == "process-ready":
        result = _run(_process_ready(settings, args.namespace_version))
    else:
        result = _run(_import(settings, args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
