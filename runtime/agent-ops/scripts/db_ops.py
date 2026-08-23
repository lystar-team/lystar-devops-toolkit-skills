#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import db_core as core
import registry_store
import result_store
from config_store import FileTransaction, load_toml, lock_files, write_toml


CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = CONFIG_HOME / "agent-ops"
DATABASES_FILE = CONFIG_DIR / "databases.toml"
PROJECTS_DIR = CONFIG_DIR / "projects"


class RegistryReferenceError(ValueError):
    """删除数据库 source/profile 前发现注册表或项目绑定依赖。"""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(core.to_json_value(payload), ensure_ascii=False, separators=(",", ":")))


def databases_config() -> dict[str, Any]:
    config = load_toml(DATABASES_FILE)
    config.setdefault("profiles", {})
    config.setdefault("aliases", {})
    return config


def save_databases(config: dict[str, Any]) -> None:
    write_toml(DATABASES_FILE, config)


def canonical_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def project_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return PROJECTS_DIR / f"{digest}.toml"


def project_config(root: Path) -> dict[str, Any]:
    config = load_toml(project_path(root))
    config.setdefault("project_root", str(root))
    config.setdefault("default_source", "")
    config.setdefault("bindings", {})
    return config


def save_project(root: Path, config: dict[str, Any]) -> None:
    config["project_root"] = str(root)
    write_toml(project_path(root), config)


def db_registry() -> registry_store.RegistryStore:
    registry_file = os.environ.get("DBX_REGISTRY_FILE") or str(DATABASES_FILE.with_name("ops.toml"))
    revision_dir = os.environ.get("DBX_REGISTRY_REVISION_DIR") or None
    return registry_store.RegistryStore(registry_file=registry_file, revision_dir=revision_dir)


def project_source_impacts(profile_id: str) -> list[dict[str, Any]]:
    impacts: list[dict[str, Any]] = []
    if not PROJECTS_DIR.exists():
        return impacts
    for path in sorted(PROJECTS_DIR.glob("*.toml")):
        try:
            config = load_toml(path)
        except (OSError, ValueError):
            continue
        bindings = config.get("bindings", {})
        matching_bindings = sorted(
            str(key) for key, value in bindings.items() if str(value) == profile_id
        ) if isinstance(bindings, dict) else []
        default_source = str(config.get("default_source") or "")
        if default_source == profile_id or matching_bindings:
            impacts.append({
                "path": str(path),
                "project_root": str(config.get("project_root") or ""),
                "default_source": default_source,
                "bindings": matching_bindings,
            })
    return impacts


def clear_project_source_impacts(profile_id: str, impacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleared: list[dict[str, Any]] = []
    for impact in impacts:
        path = Path(str(impact.get("path") or ""))
        if not str(path):
            continue
        config = load_toml(path)
        changed = False
        if str(config.get("default_source") or "") == profile_id:
            config["default_source"] = ""
            changed = True
        bindings = config.get("bindings")
        if isinstance(bindings, dict):
            removed = [key for key, value in bindings.items() if str(value) == profile_id]
            for key in removed:
                del bindings[key]
            changed = changed or bool(removed)
        if changed:
            write_toml(path, config)
            cleared.append({
                "path": str(path),
                "project_root": str(config.get("project_root") or ""),
            })
    return cleared


def reconcile_db_registry(
    source: str,
    profile_id: str,
    removed_aliases: list[str],
    remaining_aliases: list[str],
    confirm: bool,
    document: dict[str, Any],
    project_impacts: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    removing_profile = not remaining_aliases
    impact = registry_store.reference_impact(
        document,
        db_sources=set(removed_aliases) | {source},
        db_profiles={profile_id} if removing_profile else set(),
    )
    if not removing_profile:
        project_impacts = []
    result: dict[str, Any] = {
        "target": {
            "kind": "db_profile" if removing_profile else "db_source",
            "source": source,
            "profile": profile_id,
            "removed_aliases": removed_aliases,
            "remaining_aliases": remaining_aliases,
        },
        "impact": {
            **impact,
            "project_bindings": project_impacts,
        },
        "synced": [],
        "orphaned": [],
        "project_bindings_cleared": [],
        "confirmation_required": False,
        "registry_revision": document.get("revision", 0),
    }
    has_impact = impact["has_references"] or bool(project_impacts)
    if not has_impact:
        result["status"] = "ready"
        return result, False

    if not removing_profile:
        replacement = sorted(remaining_aliases)[0]
        changes = registry_store.synchronize_references(
            document,
            {alias: replacement for alias in removed_aliases},
            {
                "deployments": ("db_source", "db_sources"),
                "backup_assets": ("db_source",),
                "relations": ("source_id", "target_id"),
            },
        )
        result["status"] = "synced"
        result["synced"] = changes
        return result, bool(changes)

    if not confirm:
        result["status"] = "blocked"
        result["confirmation_required"] = True
        result["confirmation_hint"] = "dbx source remove --confirm <source>"
        raise RegistryReferenceError(
            f"cannot remove database profile {profile_id}; references exist",
            result,
        )

    marked = registry_store.mark_references_orphaned(
        document,
        impact,
        f"Database profile removed: {profile_id}",
    )
    result["status"] = "orphaned" if marked or project_impacts else "removed"
    result["orphaned"] = marked
    return result, bool(marked)


def datasource_identity(item: core.DataSource | dict[str, Any]) -> tuple[str, str, int, str, str]:
    if isinstance(item, dict):
        return (
            core.normalize_engine(str(item.get("db_type", item.get("engine", "")))),
            str(item["host"]),
            int(item["port"]),
            str(item["database"]),
            str(item["user"]),
        )
    return item.engine, item.host, int(item.port), item.database, item.user


def make_profile_id(identity: tuple[str, str, int, str, str]) -> str:
    digest = hashlib.sha256("\0".join(map(str, identity)).encode()).hexdigest()[:12]
    return f"db_{digest}"


def find_profile(config: dict[str, Any], item: core.DataSource | dict[str, Any]) -> str | None:
    identity = datasource_identity(item)
    for profile_id, profile in config["profiles"].items():
        if datasource_identity(profile) == identity:
            return profile_id
    return None


def aliases_for(config: dict[str, Any], profile_id: str) -> list[str]:
    return sorted(alias for alias, target in config["aliases"].items() if target == profile_id)


def safe_alias(value: str) -> str:
    alias = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return alias or "database"


def bind_alias(config: dict[str, Any], requested: str, profile_id: str) -> str:
    base = safe_alias(requested)
    existing = config["aliases"].get(base)
    if existing in (None, profile_id):
        config["aliases"][base] = profile_id
        return base
    alias = f"{base}-{profile_id[-6:]}"
    config["aliases"][alias] = profile_id
    return alias


def profile_from_datasource(item: core.DataSource, old: dict[str, Any] | None = None) -> dict[str, Any]:
    old = old or {}
    version = item.version or str(old.get("version", ""))
    raw_parts = item.version_parts or tuple(int(part) for part in old.get("version_parts", []))
    return {
        "engine": item.engine,
        "db_type": item.engine,
        "host": item.host,
        "port": int(item.port),
        "database": item.database,
        "user": item.user,
        "password": item.password if item.password else old.get("password", ""),
        "origin": item.source,
        "version": version,
        "version_parts": list(raw_parts),
        "version_status": item.version_status if item.version_status != "not_probed" else old.get("version_status", "not_probed"),
        "version_error": item.version_error or old.get("version_error", ""),
        "connection_status": item.connection_status if item.connection_status != "unknown" else old.get("connection_status", "unknown"),
        "version_checked_at": item.version_checked_at or old.get("version_checked_at", ""),
    }


def datasource_from_profile(profile_id: str, profile: dict[str, Any]) -> core.DataSource:
    return core.DataSource(
        engine=core.normalize_engine(str(profile.get("db_type", profile.get("engine", "")))),
        host=str(profile["host"]),
        port=int(profile["port"]),
        database=str(profile["database"]),
        user=str(profile["user"]),
        password=str(profile.get("password", "")),
        source=f"registry:{profile_id}",
        framework="registry",
        name=profile_id,
        version=str(profile.get("version", "")),
        version_parts=tuple(int(part) for part in profile.get("version_parts", [])),
        version_status=str(profile.get("version_status", "not_probed")),
        version_error=str(profile.get("version_error", "")),
        connection_status=str(profile.get("connection_status", "unknown")),
        version_checked_at=str(profile.get("version_checked_at", "")),
    )


def probe_profile_fields(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": str(probe.get("version", "")),
        "version_parts": [int(part) for part in probe.get("version_parts", [])],
        "version_status": str(probe.get("version_status", "not_probed")),
        "version_error": str(probe.get("version_error", "")),
        "connection_status": str(probe.get("connection_status", "unknown")),
        "version_checked_at": str(probe.get("version_checked_at", "")),
    }


def datasource_result(
    profile_id: str,
    datasource: core.DataSource,
    result: dict[str, Any],
) -> dict[str, Any]:
    metadata = core.model_datasource(datasource)
    metadata["profile"] = profile_id
    for key in (
        "version", "version_parts", "version_status", "version_error",
        "connection_status", "version_checked_at",
    ):
        if key in result:
            metadata[key] = result[key]
    return {"datasource": metadata, **result}


def persist_probe(profile_id: str, probe: dict[str, Any]) -> None:
    with lock_files(DATABASES_FILE):
        config = databases_config()
        profile = config["profiles"].get(profile_id)
        if not profile:
            return
        profile.update(probe_profile_fields(probe))
        config["profiles"][profile_id] = profile
        save_databases(config)


def discovery_args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=str(root),
        framework="auto",
        config_file="",
        profile="",
        name="default",
        db_type="",
        url="",
        host="",
        port=None,
        database="",
        user="",
        password="",
    )


def discovery_key(item: core.DataSource, root: Path) -> str:
    source, marker, suffix = item.source.partition("#")
    source_path = Path(source)
    try:
        source_text = source_path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        source_text = source
    parts = [item.framework, source_text, item.profile or "default", item.name]
    if marker:
        parts.append(suffix)
    return ":".join(parts)


def preferred_alias(item: core.DataSource) -> str:
    generic = {"default", "primary", "database_url", "db_url"}
    name = safe_alias(item.name)
    if name in generic:
        name = safe_alias(item.database)
    if item.profile:
        name = f"{name}-{safe_alias(item.profile)}"
    return name


def sync_discovered(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    candidates, _framework = core.collect_candidates(discovery_args(root))
    with lock_files(DATABASES_FILE, project_path(root)):
        database_cfg = databases_config()
        project_cfg = project_config(root)
        rows: list[dict[str, Any]] = []

        for item in candidates:
            profile_id = find_profile(database_cfg, item) or make_profile_id(datasource_identity(item))
            old = database_cfg["profiles"].get(profile_id)
            database_cfg["profiles"][profile_id] = profile_from_datasource(item, old)
            alias = bind_alias(database_cfg, preferred_alias(item), profile_id)
            key = discovery_key(item, root)
            project_cfg["bindings"][key] = profile_id
            saved_profile = database_cfg["profiles"][profile_id]
            rows.append({
                "source": key,
                "profile": profile_id,
                "alias": alias,
                "engine": saved_profile["engine"],
                "db_type": saved_profile.get("db_type", saved_profile["engine"]),
                "host": saved_profile["host"],
                "port": saved_profile["port"],
                "database": saved_profile["database"],
                "user": saved_profile["user"],
                "origin": item.source,
                **probe_profile_fields(saved_profile),
            })

        save_databases(database_cfg)
        save_project(root, project_cfg)
    return rows, database_cfg, project_cfg


def resolve_reference(
    reference: str,
    rows: list[dict[str, Any]],
    database_cfg: dict[str, Any],
    project_cfg: dict[str, Any],
) -> str | None:
    if not reference:
        return None
    if reference in database_cfg["profiles"]:
        return reference
    if reference in database_cfg["aliases"]:
        return str(database_cfg["aliases"][reference])
    if reference in project_cfg["bindings"]:
        return str(project_cfg["bindings"][reference])
    matches = [row["profile"] for row in rows if row["source"] == reference or row["alias"] == reference]
    return matches[0] if len(set(matches)) == 1 else None


def candidate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": row["source"],
            "profile": row["profile"],
            "alias": row["alias"],
            "db": f"{row['db_type']} {row['database']}@{row['host']}:{row['port']}",
            "db_type": row["db_type"],
            "engine": row["engine"],
            "user": row["user"],
            "version": row.get("version", ""),
            "version_parts": row.get("version_parts", []),
            "version_status": row.get("version_status", "not_probed"),
            "connection_status": row.get("connection_status", "unknown"),
        }
        for row in rows
    ]


def resolve_datasource(root: Path, requested: str) -> tuple[str, core.DataSource]:
    rows, database_cfg, project_cfg = sync_discovered(root)
    profile_id = resolve_reference(requested, rows, database_cfg, project_cfg)
    if requested and not profile_id:
        raise ValueError(f"unknown datasource: {requested}")

    if not profile_id:
        saved = str(project_cfg.get("default_source", ""))
        if saved:
            if saved not in database_cfg["profiles"]:
                raise ValueError(f"saved datasource no longer exists: {saved}")
            profile_id = saved
        else:
            discovered_profiles = list(dict.fromkeys(row["profile"] for row in rows))
            if len(discovered_profiles) == 1:
                profile_id = discovered_profiles[0]
            elif len(discovered_profiles) > 1:
                raise RuntimeError("multiple datasources found; run dbx sources and dbx use <source>")
            else:
                raise RuntimeError("no datasource found; run dbx source add or add project datasource config")

    profile = database_cfg["profiles"].get(profile_id)
    if not profile:
        raise ValueError(f"datasource profile no longer exists: {profile_id}")
    return profile_id, datasource_from_profile(profile_id, profile)


def handle_sources(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    rows, _database_cfg, project_cfg = sync_discovered(root)
    print_json({
        "project": str(root),
        "default": project_cfg.get("default_source", ""),
        "sources": candidate_summary(rows),
    })
    return 0


def handle_use(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    rows, database_cfg, _project_cfg = sync_discovered(root)
    with lock_files(project_path(root)):
        project_cfg = project_config(root)
        if args.clear:
            project_cfg["default_source"] = ""
            save_project(root, project_cfg)
            print_json({"project": str(root), "default": ""})
            return 0
        if not args.source:
            raise ValueError("provide a datasource alias, profile, or discovery source")
        profile_id = resolve_reference(args.source, rows, database_cfg, project_cfg)
        if not profile_id:
            raise ValueError(f"unknown datasource: {args.source}")
        project_cfg["default_source"] = profile_id
        save_project(root, project_cfg)
    selected_profile = database_cfg["profiles"].get(profile_id, {})
    print_json({
        "project": str(root),
        "default": profile_id,
        "aliases": aliases_for(database_cfg, profile_id),
        "db_type": selected_profile.get("db_type", selected_profile.get("engine", "")),
        "engine": selected_profile.get("engine", ""),
        "version": selected_profile.get("version", ""),
        "version_parts": selected_profile.get("version_parts", []),
        "version_status": selected_profile.get("version_status", "not_probed"),
        "connection_status": selected_profile.get("connection_status", "unknown"),
    })
    return 0


def manual_datasource(args: argparse.Namespace) -> core.DataSource:
    if args.url:
        parsed = core.parse_connection_url(args.url)
        if not parsed:
            raise ValueError("invalid database URL")
        engine = core.normalize_engine(args.db_type) if args.db_type else parsed["engine"]
        return core.DataSource(
            engine=engine,
            host=args.host or parsed["host"],
            port=args.port or (parsed["port"] if not args.db_type else core.default_port(engine)),
            database=args.database or parsed["database"],
            user=args.user or parsed.get("user", ""),
            password=args.password if args.password is not None else parsed.get("password", ""),
            source=f"manual:{args.alias}",
            framework="manual",
            name=args.alias,
            url=args.url,
        )
    if not args.db_type or not args.database or not args.user:
        raise ValueError("source add requires --type, --database, and --user")
    engine = core.normalize_engine(args.db_type)
    return core.DataSource(
        engine=engine,
        host=args.host or "127.0.0.1",
        port=args.port or core.default_port(engine),
        database=args.database,
        user=args.user,
        password=args.password or "",
        source=f"manual:{args.alias}",
        framework="manual",
        name=args.alias,
    )


def handle_source_add(args: argparse.Namespace) -> int:
    item = manual_datasource(args)
    with lock_files(DATABASES_FILE):
        config = databases_config()
        profile_id = find_profile(config, item) or make_profile_id(datasource_identity(item))
        old = config["profiles"].get(profile_id)
        existing_alias = config["aliases"].get(args.alias)
        if existing_alias and existing_alias != profile_id:
            raise ValueError(f"datasource alias already points elsewhere: {args.alias}")
        config["profiles"][profile_id] = profile_from_datasource(item, old)
        config["aliases"][args.alias] = profile_id
        save_databases(config)
    print_json({
        "profile": profile_id,
        "alias": args.alias,
        "db_type": item.engine,
        "engine": item.engine,
        "version": item.version,
        "version_parts": list(item.version_parts),
        "version_status": item.version_status,
        "connection_status": item.connection_status,
        "reused": old is not None,
    })
    return 0


def source_view(profile_id: str, profile: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": profile_id,
        "aliases": aliases_for(config, profile_id),
        "db_type": profile.get("db_type", profile["engine"]),
        "engine": profile["engine"],
        "host": profile["host"],
        "port": profile["port"],
        "database": profile["database"],
        "user": profile["user"],
        "password": "***" if profile.get("password") else "",
        "origin": profile.get("origin", ""),
        "version": profile.get("version", ""),
        "version_parts": profile.get("version_parts", []),
        "version_status": profile.get("version_status", "not_probed"),
        "version_error": profile.get("version_error", ""),
        "connection_status": profile.get("connection_status", "unknown"),
        "version_checked_at": profile.get("version_checked_at", ""),
    }


def handle_source_list(_args: argparse.Namespace) -> int:
    config = databases_config()
    print_json({"sources": [source_view(profile_id, profile, config) for profile_id, profile in sorted(config["profiles"].items())]})
    return 0


def handle_source_show(args: argparse.Namespace) -> int:
    config = databases_config()
    profile_id = config["aliases"].get(args.source, args.source)
    profile = config["profiles"].get(profile_id)
    if not profile:
        raise ValueError(f"unknown datasource: {args.source}")
    datasource = datasource_from_profile(profile_id, profile)
    probe = core.probe_datasource(datasource)
    with lock_files(DATABASES_FILE):
        config = databases_config()
        stored = config["profiles"].get(profile_id)
        if stored:
            stored.update(probe_profile_fields(probe))
            config["profiles"][profile_id] = stored
            save_databases(config)
            profile = stored
    print_json(source_view(profile_id, profile, config))
    return 0


def handle_source_remove(args: argparse.Namespace) -> int:
    store = db_registry()
    transaction_paths = [DATABASES_FILE, store.registry_file, *PROJECTS_DIR.glob("*.toml")]
    with FileTransaction(
        transaction_paths,
        glob_paths=((store.revision_dir, "revision-*.toml"),),
    ) as transaction:
        config = databases_config()
        profile_id = config["aliases"].get(args.source)
        removed_aliases: list[str] = []
        if profile_id:
            removed_aliases = [args.source]
        elif args.source in config["profiles"]:
            profile_id = args.source
            removed_aliases = aliases_for(config, profile_id)
        if not profile_id:
            raise ValueError(f"unknown datasource: {args.source}")
        all_aliases = aliases_for(config, profile_id)
        remaining = [alias for alias in all_aliases if alias not in removed_aliases]
        document = store.load_locked()
        project_impacts = project_source_impacts(profile_id) if not remaining else []
        registry_result, registry_changed = reconcile_db_registry(
            args.source,
            profile_id,
            removed_aliases,
            remaining,
            bool(getattr(args, "confirm", False)),
            document,
            project_impacts,
        )
        for alias in removed_aliases:
            config["aliases"].pop(alias, None)
        if not remaining:
            config["profiles"].pop(profile_id, None)
        registry_result["project_bindings_cleared"] = clear_project_source_impacts(
            profile_id,
            project_impacts,
        )
        save_databases(config)
        if registry_changed:
            committed = store.save_locked(document)
            registry_result["registry_revision"] = committed.get("revision", 0)
        transaction.commit()
    print_json({
        "removed": args.source,
        "profile": profile_id,
        "remaining_aliases": remaining,
        "registry": registry_result,
    })
    return 0


def query_result(result: dict[str, Any], max_cell_chars: int) -> dict[str, Any]:
    model = core.model_query_result(result, max_cell_chars, "array", False)
    payload = {
        "columns": model["columns"],
        "rows": model["rows"],
        "has_more": model["has_more"],
    }
    if model.get("cells_truncated"):
        payload["cells_truncated"] = True
    for key in (
        "version", "version_parts", "version_status", "version_error",
        "connection_status", "version_checked_at",
    ):
        if key in result:
            payload[key] = result[key]
    return payload


def csv_cell(value: Any) -> Any:
    if value is None:
        return r"\N"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def query_csv(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    metadata = [
        f"rows={len(rows)}",
        f"has_more={str(bool(payload.get('has_more'))).lower()}",
    ]
    if payload.get("cells_truncated"):
        metadata.append("cells_truncated=true")
    output = io.StringIO()
    output.write("# " + " ".join(metadata) + "\n")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(payload.get("columns", []))
    writer.writerows([[csv_cell(value) for value in row] for row in rows])
    return output.getvalue()


def print_query_result(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print_json(payload)
    else:
        sys.stdout.write(query_csv(payload))


def operation_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "query":
        return {"sql": args.sql, "limit": args.limit}
    if args.command == "exec":
        return {"sql": args.sql, "transaction": getattr(args, "transaction", "commit")}
    if args.command == "import":
        return {
            "sql_file": str(Path(args.sql_file).expanduser().resolve()),
            "transaction": getattr(args, "transaction", "commit"),
        }
    if args.command == "export":
        return {
            "out_file": str(Path(args.out_file).expanduser().resolve()),
            "tables": args.tables,
            "schema": args.schema,
            "schema_only": args.schema_only,
            "data_only": args.data_only,
        }
    return {}


def save_operation_result(
    args: argparse.Namespace,
    root: Path,
    profile_id: str,
    result: dict[str, Any],
    success: bool,
) -> None:
    result_store.save_snapshot(
        "db",
        args.command,
        root,
        operation_request(args),
        core.to_json_value(result),
        success,
        profile_id or args.source,
    )


def handle_query(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    profile_id, datasource = resolve_datasource(root, args.source)
    core.enforce_query_read_only(args.sql, False)
    result = query_result(core.run_query(datasource, args.sql, args.limit), args.max_cell_chars)
    persist_probe(profile_id, result)
    result = datasource_result(profile_id, datasource, result)
    save_operation_result(args, root, profile_id, result, True)
    print_query_result(result, args.json)
    return 0


def handle_exec(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    profile_id, datasource = resolve_datasource(root, args.source)
    result = core.run_non_query(datasource, args.sql, getattr(args, "transaction", "commit"))
    persist_probe(profile_id, result)
    result = datasource_result(profile_id, datasource, result)
    save_operation_result(args, root, profile_id, result, True)
    print_json(result)
    return 0


def handle_import(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    profile_id, datasource = resolve_datasource(root, args.source)
    sql_file = Path(args.sql_file).expanduser().resolve()
    if not sql_file.is_file():
        raise FileNotFoundError(f"SQL file not found: {sql_file}")
    result = core.run_non_query(
        datasource,
        sql_file.read_text(encoding="utf-8"),
        getattr(args, "transaction", "commit"),
    )
    persist_probe(profile_id, result)
    result = datasource_result(profile_id, datasource, result)
    save_operation_result(args, root, profile_id, result, True)
    print_json(result)
    return 0


def handle_export(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    profile_id, datasource = resolve_datasource(root, args.source)
    export_args = SimpleNamespace(
        schema_only=args.schema_only,
        data_only=args.data_only,
        batch_size=args.batch_size,
        out_file=args.out_file,
        tables=args.tables,
        schema=args.schema,
        include_drop=args.include_drop,
    )
    result = core.run_export(datasource, export_args)
    persist_probe(profile_id, result)
    result = datasource_result(profile_id, datasource, result)
    save_operation_result(args, root, profile_id, result, True)
    print_json(result)
    return 0


def handle_last(args: argparse.Namespace) -> int:
    root = canonical_root(args.root)
    if args.clear:
        print_json({"cleared": result_store.clear_snapshot("db", root)})
        return 0
    snapshot = result_store.load_snapshot("db", root)
    if args.summary:
        print_json(result_store.snapshot_view(snapshot, True))
        return 0
    result = snapshot.get("result", {})
    if not args.json and snapshot.get("success") and snapshot.get("action") == "query" and "columns" in result:
        print_query_result(result, False)
    else:
        print_json(result_store.snapshot_view(snapshot, False))
    return 0


def add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=str(Path.cwd()), help="Project root; defaults to current directory")
    parser.add_argument("--source", default="", help="Datasource alias, profile, or discovery source")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared database client for AI agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources = subparsers.add_parser("sources")
    sources.add_argument("--root", default=str(Path.cwd()))
    sources.set_defaults(func=handle_sources)

    use = subparsers.add_parser("use")
    use.add_argument("source", nargs="?")
    use.add_argument("--clear", action="store_true")
    use.add_argument("--root", default=str(Path.cwd()))
    use.set_defaults(func=handle_use)

    source = subparsers.add_parser("source")
    source_commands = source.add_subparsers(dest="source_command", required=True)

    source_add = source_commands.add_parser("add")
    source_add.add_argument("alias")
    source_add.add_argument("--type", dest="db_type", choices=["mysql", "mariadb", "postgresql", "postgres"])
    source_add.add_argument("--url", default="")
    source_add.add_argument("--host", default="")
    source_add.add_argument("--port", type=int)
    source_add.add_argument("--database", default="")
    source_add.add_argument("--user", default="")
    source_add.add_argument("--password")
    source_add.set_defaults(func=handle_source_add)

    source_list = source_commands.add_parser("list")
    source_list.set_defaults(func=handle_source_list)

    source_show = source_commands.add_parser("show")
    source_show.add_argument("source")
    source_show.set_defaults(func=handle_source_show)

    source_remove = source_commands.add_parser("remove")
    source_remove.add_argument("source")
    source_remove.add_argument(
        "--confirm",
        action="store_true",
        help="确认删除最后一个 alias/profile，并将注册表依赖标记为 orphaned",
    )
    source_remove.add_argument("--json", action="store_true", help="Return the structured JSON result")
    source_remove.set_defaults(func=handle_source_remove)

    query = subparsers.add_parser("query")
    query.add_argument("sql")
    add_project_args(query)
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--max-cell-chars", type=int, default=300)
    query.add_argument("--json", action="store_true", help="Return the structured JSON result")
    query.set_defaults(func=handle_query)

    execute = subparsers.add_parser("exec")
    execute.add_argument("sql")
    add_project_args(execute)
    execute.add_argument("--transaction", choices=["commit", "rollback"], default="commit")
    execute.set_defaults(func=handle_exec)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("sql_file")
    add_project_args(import_parser)
    import_parser.add_argument("--transaction", choices=["commit", "rollback"], default="commit")
    import_parser.set_defaults(func=handle_import)

    export = subparsers.add_parser("export")
    export.add_argument("out_file")
    add_project_args(export)
    export.add_argument("--tables", default="")
    export.add_argument("--schema", default="public")
    export.add_argument("--schema-only", action="store_true")
    export.add_argument("--data-only", action="store_true")
    export.add_argument("--include-drop", action="store_true")
    export.add_argument("--batch-size", type=int, default=200)
    export.set_defaults(func=handle_export)

    last = subparsers.add_parser("last")
    last.add_argument("--root", default=str(Path.cwd()))
    last_mode = last.add_mutually_exclusive_group()
    last_mode.add_argument("--summary", action="store_true")
    last_mode.add_argument("--clear", action="store_true")
    last_mode.add_argument("--json", action="store_true", help="Return the structured JSON snapshot")
    last.set_defaults(func=handle_last)
    return parser


def main() -> int:
    result_store.cleanup()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        if args.command in {"query", "exec", "import", "export"}:
            root = canonical_root(args.root)
            failure = {"error": str(exc), **getattr(exc, "details", {})}
            save_operation_result(args, root, "", failure, False)
        print_json({"error": str(exc), **getattr(exc, "details", {})})
        return 1


if __name__ == "__main__":
    sys.exit(main())
