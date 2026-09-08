#!/usr/bin/env python3
"""LYStar Redis DB inspection and reservation runtime."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Mapping

try:
    import redis
except ImportError:  # Help/version commands must work before optional runtime deps are installed.
    redis = None

from config_store import lock_files, load_toml, write_toml
import paths
from result_store import canonical_project_root, save_snapshot


SCHEMA_VERSION = 1
TOOL_NAME = "redisx"
DEFAULT_DATABASE_COUNT = 16
PROFILE_CONFIG = "redis-ops.toml"
RESERVATION_CONFIG = "redis-reservations.toml"
PROJECT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


class RedisOpsError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def config_path() -> Path:
    return paths.config_home() / PROFILE_CONFIG


def reservation_path() -> Path:
    return paths.config_home() / RESERVATION_CONFIG


def default_config() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "default_profile": "", "profiles": {}}


def load_config() -> dict[str, Any]:
    raw = load_toml(config_path())
    config = default_config()
    if raw:
        config.update({key: value for key, value in raw.items() if key != "profiles"})
        config["profiles"] = dict(raw.get("profiles", {}))
    if not isinstance(config["profiles"], dict):
        raise RedisOpsError("redis-ops.toml 的 profiles 必须是表")
    return config


def load_reservations() -> dict[str, Any]:
    raw = load_toml(reservation_path())
    if not raw:
        return {"schema_version": SCHEMA_VERSION, "reservations": {}}
    reservations = raw.get("reservations", {})
    if not isinstance(reservations, dict):
        raise RedisOpsError("redis-reservations.toml 的 reservations 必须是表")
    return {"schema_version": SCHEMA_VERSION, "reservations": dict(reservations)}


def normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    host = str(profile.get("host", "")).strip()
    if not host:
        raise RedisOpsError("Redis profile 缺少 host")
    port = int(profile.get("port", 6379))
    if not 1 <= port <= 65535:
        raise RedisOpsError("Redis port 必须在 1-65535 范围内")
    username = str(profile.get("username", "")).strip()
    if str(profile.get("username_env", "")).strip() or str(profile.get("password_env", "")).strip():
        raise RedisOpsError("username_env/password_env 已废弃，请直接配置 username/password")
    password = str(profile.get("password", ""))
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "tls": bool(profile.get("tls", False)),
        "database_count": int(profile.get("database_count", DEFAULT_DATABASE_COUNT)),
        "socket_timeout": float(profile.get("socket_timeout", 5.0)),
    }


def resolve_settings(args: argparse.Namespace | Mapping[str, Any]) -> dict[str, Any]:
    values = vars(args) if isinstance(args, argparse.Namespace) else dict(args)
    profile_id = str(values.get("profile", "")).strip()
    config = load_config()
    if not profile_id:
        profile_id = str(config.get("default_profile", "")).strip()
    raw_profile: dict[str, Any] = {}
    if profile_id:
        raw_profile = dict(config.get("profiles", {}).get(profile_id, {}))
        if not raw_profile and not values.get("host"):
            raise RedisOpsError(f"Redis profile 不存在：{profile_id}")
    for key in ("host", "port", "username", "password", "tls", "database_count", "socket_timeout"):
        if values.get(key) not in (None, ""):
            raw_profile[key] = values[key]
    settings = normalize_profile(raw_profile)
    settings["profile"] = profile_id
    settings["password"] = str(settings.get("password") or "")
    settings["database_count"] = max(1, min(settings["database_count"], 4096))
    return settings


def safe_public_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in settings.items()
        if key not in {"password"}
    }


def connect(settings: Mapping[str, Any], database: int = 0) -> redis.Redis:
    if redis is None:
        raise RedisOpsError("Redis Python 依赖未安装，请安装 redis 依赖")
    return redis.Redis(
        host=str(settings["host"]),
        port=int(settings["port"]),
        username=str(settings.get("username") or "") or None,
        password=str(settings.get("password") or "") or None,
        db=database,
        ssl=bool(settings.get("tls", False)),
        socket_timeout=float(settings.get("socket_timeout", 5.0)),
        socket_connect_timeout=float(settings.get("socket_timeout", 5.0)),
        decode_responses=True,
    )


def database_count(settings: Mapping[str, Any]) -> tuple[int, str | None]:
    configured = int(settings.get("database_count", DEFAULT_DATABASE_COUNT))
    try:
        value = connect(settings).config_get("databases").get("databases")
        if value is not None:
            return max(1, min(int(value), 4096)), None
    except (redis.RedisError, ValueError, TypeError) as exc:
        return configured, f"无法读取 Redis databases 配置，使用 profile 值 {configured}: {exc}"
    return configured, None


def reserved_by_profile(profile_id: str) -> dict[int, dict[str, Any]]:
    raw = load_reservations().get("reservations", {})
    result: dict[int, dict[str, Any]] = {}
    for project_id, item in raw.items():
        if not isinstance(item, dict) or str(item.get("profile", "")) != profile_id:
            continue
        try:
            database = int(item["database"])
        except (KeyError, TypeError, ValueError):
            continue
        result[database] = {"project_id": str(project_id), **item}
    return result


def scan_databases(settings: Mapping[str, Any], start: int = 0, end: int | None = None) -> dict[str, Any]:
    count, warning = database_count(settings)
    last = count - 1 if end is None else min(int(end), count - 1)
    first = max(0, int(start))
    if first > last:
        raise RedisOpsError("Redis DB 扫描范围为空")
    reservations = reserved_by_profile(str(settings.get("profile", "")))
    connection = connect(settings)
    rows: list[dict[str, Any]] = []
    try:
        connection.ping()
        for database in range(first, last + 1):
            connection.select(database)
            keys = int(connection.dbsize())
            reservation = reservations.get(database)
            rows.append(
                {
                    "database": database,
                    "keys": keys,
                    "empty": keys == 0,
                    "reserved": reservation is not None,
                    "reserved_by": reservation.get("project_id") if reservation else "",
                    "available": keys == 0 and reservation is None,
                }
            )
    except redis.RedisError as exc:
        raise RedisOpsError("Redis DB 扫描失败", {"error": str(exc), "settings": safe_public_settings(settings)}) from exc
    finally:
        connection.close()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "redis.db.list",
        "status": "ok",
        "connection_status": "ok",
        "settings": safe_public_settings(settings),
        "database_count": count,
        "warning": warning,
        "databases": rows,
    }


def free_databases(settings: Mapping[str, Any], start: int = 0, end: int | None = None) -> dict[str, Any]:
    result = scan_databases(settings, start=start, end=end)
    available = [item for item in result["databases"] if item["available"]]
    result["kind"] = "redis.db.find-free"
    result["available"] = available
    result["selected"] = available[0]["database"] if available else None
    result["status"] = "available" if available else "blocked"
    return result


def validate_project_id(value: str) -> str:
    project_id = str(value or "").strip()
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise RedisOpsError("project-id 只能包含字母、数字、点、下划线和短横线")
    return project_id


def reserve_database(
    settings: Mapping[str, Any],
    project_id: str,
    database: int | None = None,
    start: int = 0,
    end: int | None = None,
) -> dict[str, Any]:
    project_id = validate_project_id(project_id)
    profile_id = str(settings.get("profile", ""))
    if not profile_id:
        raise RedisOpsError("预留 Redis DB 必须使用已登记 profile")
    with lock_files(reservation_path()):
        current = load_reservations()
        reservations = dict(current.get("reservations", {}))
        existing = reservations.get(project_id)
        if isinstance(existing, dict) and str(existing.get("profile", "")) == profile_id:
            return {"schema_version": SCHEMA_VERSION, "kind": "redis.db.reserve", "status": "already_reserved", "reservation": existing}
        occupied = reserved_by_profile(profile_id)
        if database is None:
            scanned = free_databases(settings, start=start, end=end)
            database = scanned.get("selected")
            if database is None:
                raise RedisOpsError("没有可用的空闲 Redis DB", {"scan": scanned})
        database = int(database)
        if database in occupied and occupied[database].get("project_id") != project_id:
            raise RedisOpsError("Redis DB 已被本地状态预留", {"database": database, "reserved_by": occupied[database].get("project_id")})
        scanned = scan_databases(settings, start=database, end=database)
        row = scanned["databases"][0]
        if not row["empty"]:
            raise RedisOpsError("Redis DB 不是空闲索引，拒绝预留", {"database": database, "keys": row["keys"]})
        reservation = {
            "profile": profile_id,
            "host": str(settings["host"]),
            "port": int(settings["port"]),
            "database": database,
            "reserved_at": utc_now(),
        }
        reservations[project_id] = reservation
        write_toml(reservation_path(), {"schema_version": SCHEMA_VERSION, "reservations": reservations})
    return {"schema_version": SCHEMA_VERSION, "kind": "redis.db.reserve", "status": "reserved", "reservation": reservation}


def reassign_database(
    settings: Mapping[str, Any],
    project_id: str,
    database: int | None = None,
    start: int = 0,
    end: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    project_id = validate_project_id(project_id)
    profile_id = str(settings.get("profile", ""))
    if not profile_id:
        raise RedisOpsError("迁移 Redis DB 必须使用已登记 profile")
    with lock_files(reservation_path()):
        current = load_reservations()
        reservations = dict(current.get("reservations", {}))
        existing = reservations.get(project_id)
        if not isinstance(existing, dict) or str(existing.get("profile", "")) != profile_id:
            raise RedisOpsError("找不到当前项目在该 Redis profile 下的预留记录", {"project_id": project_id, "profile": profile_id})
        try:
            old_database = int(existing["database"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RedisOpsError("当前 Redis 预留记录的 database 无效", {"project_id": project_id}) from exc
        occupied = reserved_by_profile(profile_id)
        if database is None:
            scanned = free_databases(settings, start=start, end=end)
            database = scanned.get("selected")
            if database is None:
                raise RedisOpsError("没有可用的空闲 Redis DB", {"scan": scanned})
        target_database = int(database)
        if target_database == old_database:
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": "redis.db.reassign",
                "status": "already_reserved",
                "project_id": project_id,
                "old_database": old_database,
                "database": target_database,
                "reservation": existing,
            }
        if target_database in occupied and occupied[target_database].get("project_id") != project_id:
            raise RedisOpsError("目标 Redis DB 已被本地状态预留", {"database": target_database, "reserved_by": occupied[target_database].get("project_id")})
        scanned = scan_databases(settings, start=target_database, end=target_database)
        row = scanned["databases"][0]
        if not row["empty"]:
            raise RedisOpsError("目标 Redis DB 不是空闲索引，拒绝迁移", {"database": target_database, "keys": row["keys"]})
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": "redis.db.reassign",
            "project_id": project_id,
            "old_database": old_database,
            "database": target_database,
            "reservation_before": existing,
            "target": row,
        }
        if not confirm:
            return {**result, "status": "dry_run"}
        reservation = dict(existing)
        reservation["database"] = target_database
        reservation["migrated_at"] = utc_now()
        reservations[project_id] = reservation
        write_toml(reservation_path(), {"schema_version": SCHEMA_VERSION, "reservations": reservations})
    return {**result, "status": "reassigned", "reservation": reservation, "released_database": old_database}


def verify_database(settings: Mapping[str, Any], database: int) -> dict[str, Any]:
    database = int(database)
    count, warning = database_count(settings)
    if database < 0 or database >= count:
        raise RedisOpsError("Redis DB 超出服务器 databases 范围", {"database": database, "database_count": count})
    connection = connect(settings, database=database)
    try:
        connection.ping()
        keys = int(connection.dbsize())
    except redis.RedisError as exc:
        raise RedisOpsError("Redis DB 验证失败", {"error": str(exc), "settings": safe_public_settings(settings)}) from exc
    finally:
        connection.close()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "redis.db.verify",
        "status": "ok",
        "connection_status": "ok",
        "settings": safe_public_settings(settings),
        "database": database,
        "keys": keys,
        "empty": keys == 0,
        "warning": warning,
    }


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    settings = resolve_settings(args)
    result = verify_database(settings, 0)
    result["kind"] = "redis.doctor"
    result["profile"] = settings.get("profile", "")
    return result


def request_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    allowed = ("command", "profile", "host", "port", "database", "start", "end", "project_id")
    return {key: getattr(args, key) for key in allowed if hasattr(args, key) and getattr(args, key) not in (None, "")}


def dump(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    print(f"{payload.get('kind', TOOL_NAME)}: {payload.get('status', 'unknown')}")
    if payload.get("selected") is not None:
        print(f"selected_db: {payload['selected']}")
    if payload.get("database") is not None:
        print(f"database: {payload['database']}")
    if payload.get("error"):
        print(f"error: {payload['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile")
    common.add_argument("--host")
    common.add_argument("--port", type=int)
    common.add_argument("--username")
    common.add_argument("--password")
    common.add_argument("--tls", action="store_true")
    common.add_argument("--database-count", type=int)
    common.add_argument("--socket-timeout", type=float)

    doctor_parser = sub.add_parser("doctor", parents=[common])
    doctor_parser.set_defaults(func=doctor)
    list_parser = sub.add_parser("db-list", parents=[common])
    list_parser.add_argument("--start", type=int, default=0)
    list_parser.add_argument("--end", type=int)
    list_parser.set_defaults(func=lambda args: scan_databases(resolve_settings(args), args.start, args.end))
    free_parser = sub.add_parser("db-find-free", parents=[common])
    free_parser.add_argument("--start", type=int, default=0)
    free_parser.add_argument("--end", type=int)
    free_parser.set_defaults(func=lambda args: free_databases(resolve_settings(args), args.start, args.end))
    reserve_parser = sub.add_parser("db-reserve", parents=[common])
    reserve_parser.add_argument("--project-id", required=True)
    reserve_parser.add_argument("--database", type=int)
    reserve_parser.add_argument("--start", type=int, default=0)
    reserve_parser.add_argument("--end", type=int)
    reserve_parser.set_defaults(func=lambda args: reserve_database(resolve_settings(args), args.project_id, args.database, args.start, args.end))
    reassign_parser = sub.add_parser("db-reassign", parents=[common])
    reassign_parser.add_argument("--project-id", required=True)
    reassign_parser.add_argument("--database", type=int)
    reassign_parser.add_argument("--start", type=int, default=0)
    reassign_parser.add_argument("--end", type=int)
    reassign_parser.add_argument("--confirm", action="store_true")
    reassign_parser.set_defaults(func=lambda args: reassign_database(resolve_settings(args), args.project_id, args.database, args.start, args.end, args.confirm))
    verify_parser = sub.add_parser("db-verify", parents=[common])
    verify_parser.add_argument("--database", type=int, required=True)
    verify_parser.set_defaults(func=lambda args: verify_database(resolve_settings(args), args.database))
    for command_parser in (doctor_parser, list_parser, free_parser, reserve_parser, reassign_parser, verify_parser):
        command_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
        if not isinstance(payload, dict):
            raise RedisOpsError("Redis 操作返回格式错误")
        save_snapshot(
            TOOL_NAME,
            args.command,
            canonical_project_root(),
            request_snapshot(args),
            payload,
            payload.get("status") in {"ok", "available", "reserved", "already_reserved", "reassigned"},
            target=str(payload.get("database", payload.get("selected", ""))),
        )
        dump(payload, args.json)
        return 0 if payload.get("status") in {"ok", "available", "reserved", "already_reserved", "reassigned"} else 1
    except (RedisOpsError, redis.RedisError) as exc:
        payload = {"schema_version": SCHEMA_VERSION, "kind": "redis.error", "status": "failed", "error": str(exc)}
        details = getattr(exc, "details", None)
        if details:
            payload["details"] = details
        dump(payload, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
