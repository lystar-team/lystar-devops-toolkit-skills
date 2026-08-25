#!/usr/bin/env python3
"""全局运维对象注册表的最小持久化运行时。

注册表只保存项目、服务、环境和其它业务对象的引用关系，不复制 sshx/dbx
的 profile 或密码。连接配置仍由各自 Skill 维护，本模块只负责 ops.toml
及其可恢复 revision。
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Mapping

from config_store import lock_files, load_toml, write_toml
import paths


SCHEMA_VERSION = 1
DEFAULT_ENVIRONMENT = "unassigned"
COLLECTIONS = (
    "projects",
    "services",
    "environments",
    "deployments",
    "recipes",
    "backup_repositories",
    "backup_assets",
    "relations",
)
KIND_TO_COLLECTION = {
    "project": "projects",
    "service": "services",
    "environment": "environments",
    "deployment": "deployments",
    "recipe": "recipes",
    "backup_repository": "backup_repositories",
    "backup_asset": "backup_assets",
    "relation": "relations",
}
COLLECTION_TO_KIND = {collection: kind for kind, collection in KIND_TO_COLLECTION.items()}
# 这些是首版注册表的稳定字段名；未知字段仍按原样保留，便于后续 Skill
# 增量升级而不丢失用户或新版本写入的数据。
OBJECT_FIELDS = {
    "project": ("id", "name", "aliases", "local_path", "repository_source", "description"),
    "service": ("id", "name", "aliases", "project_id", "service_type", "description"),
    "environment": ("id", "name", "description"),
    "deployment": (
        "id", "project_id", "service_id", "environment", "ssh_alias", "ssh_profile",
        "db_source", "db_profile", "db_sources",
        "local_project_path", "artifact_path", "artifact_format", "artifact_sha256",
        "strategy", "recipe_id", "service_manager", "unit", "paths", "ports",
        "health_checks", "retention", "status", "management_status", "legacy_mode",
        "systemd_baseline", "observed", "creation_stages", "remote_write_performed",
        "adoption_confirmed", "adopted_at", "orphaned_at", "orphaned_reasons",
    ),
    "recipe": ("id", "name", "strategy", "stages", "checksum", "managed"),
    "backup_repository": (
        "id", "kind", "path", "default", "enabled", "description", "updated_at",
    ),
    "backup_asset": (
        "id", "project_id", "service_id", "environment", "db_source", "db_profile",
        "ssh_alias", "ssh_profile",
        "remote_path", "repository_id", "keep_last", "keep_days", "restore_verify", "kind",
        "status", "updated_at", "orphaned_at", "orphaned_reasons",
    ),
    "relation": ("id", "source_kind", "source_id", "target_kind", "target_id", "relation"),
}
REFERENCE_FIELDS = {
    "service": ("project_id",),
    "deployment": (
        "project_id", "service_id", "environment", "ssh_alias", "ssh_profile",
        "db_source", "db_profile", "db_sources", "recipe_id",
    ),
    "backup_asset": (
        "project_id", "service_id", "environment", "db_source", "db_profile",
        "ssh_alias", "ssh_profile", "repository_id",
    ),
    "relation": ("source_kind", "source_id", "target_kind", "target_id"),
}
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RELATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
REVISION_FILE_RE = re.compile(r"^revision-(\d+)\.toml$")


class RegistryError(RuntimeError):
    """注册表格式、引用或恢复失败。"""


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_registry_file(config_home: str | Path | None = None) -> Path:
    root = Path(config_home).expanduser() if config_home is not None else paths.config_home()
    return root / "ops.toml"


def default_revision_dir(state_home: str | Path | None = None) -> Path:
    root = Path(state_home).expanduser() if state_home is not None else paths.state_home()
    if state_home is None and paths.legacy_mode():
        return root / "ops" / "revisions"
    return root / "registry" / "revisions"


def empty_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": "",
        **{collection: {} for collection in COLLECTIONS},
    }


def normalize_kind(kind: str) -> str:
    value = str(kind).strip().lower()
    if value in KIND_TO_COLLECTION:
        return value
    if value in COLLECTION_TO_KIND:
        return COLLECTION_TO_KIND[value]
    raise RegistryError(f"unknown registry object kind: {kind}")


def collection_for(kind: str) -> str:
    return KIND_TO_COLLECTION[normalize_kind(kind)]


def validate_object_id(kind: str, object_id: str) -> str:
    normalized_kind = normalize_kind(kind)
    value = str(object_id).strip()
    if normalized_kind == "deployment":
        parts = value.split("/")
        if len(parts) != 3 or any(not OBJECT_ID_RE.fullmatch(part) for part in parts):
            raise RegistryError(
                "deployment id must be <project_id>/<service_id>/<environment>"
            )
        return value
    pattern = RELATION_ID_RE if normalized_kind == "relation" else OBJECT_ID_RE
    if not pattern.fullmatch(value):
        raise RegistryError(f"invalid {normalized_kind} id: {object_id}")
    return value


def deployment_identity(project_id: str, service_id: str, environment: str) -> str:
    project = validate_object_id("project", project_id)
    service = validate_object_id("service", service_id)
    env = validate_object_id("environment", environment)
    return f"{project}/{service}/{env}"


def legacy_environment(value: Any) -> str:
    """把没有环境绑定的历史对象明确标记为 unassigned。"""

    if value is None:
        return DEFAULT_ENVIRONMENT
    text = str(value).strip()
    return text or DEFAULT_ENVIRONMENT


def _copy_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{label} must be a table")
    return copy.deepcopy(dict(value))


def _normalize_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    document = _copy_mapping(payload, "registry")
    schema_version = document.get("schema_version", SCHEMA_VERSION)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise RegistryError("registry schema_version must be an integer")
    if schema_version < 1 or schema_version > SCHEMA_VERSION:
        raise RegistryError(f"unsupported registry schema_version: {schema_version}")
    document["schema_version"] = SCHEMA_VERSION

    revision = document.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RegistryError("registry revision must be a non-negative integer")
    document["revision"] = revision
    updated_at = document.get("updated_at", "")
    if updated_at is None:
        updated_at = ""
    if not isinstance(updated_at, str):
        raise RegistryError("registry updated_at must be a string")
    document["updated_at"] = updated_at

    for collection in COLLECTIONS:
        raw_objects = document.get(collection, {})
        if not isinstance(raw_objects, Mapping):
            raise RegistryError(f"registry {collection} must be a table")
        objects: dict[str, Any] = {}
        for raw_id, raw_object in raw_objects.items():
            object_id = str(raw_id)
            kind = COLLECTION_TO_KIND[collection]
            validate_object_id(kind, object_id)
            obj = _copy_mapping(raw_object, f"{collection}.{object_id}")
            declared_id = obj.get("id")
            if declared_id is not None and declared_id != object_id:
                raise RegistryError(
                    f"{collection}.{object_id}.id does not match its registry key"
                )
            obj["id"] = object_id
            objects[object_id] = obj
        document[collection] = objects
    return document


def _read_document(path: Path) -> dict[str, Any]:
    try:
        payload = load_toml(path)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    return _normalize_document(payload)


class RegistryStore:
    """读写全局注册表，并在每次写入前后保留可恢复 revision。"""

    def __init__(
        self,
        registry_file: str | Path | None = None,
        revision_dir: str | Path | None = None,
    ) -> None:
        self.registry_file = Path(registry_file).expanduser() if registry_file else default_registry_file()
        self.revision_dir = Path(revision_dir).expanduser() if revision_dir else default_revision_dir()

    def _revision_path(self, revision: int) -> Path:
        return self.revision_dir / f"revision-{revision:020d}.toml"

    def _revision_candidates(self) -> list[tuple[int, float, Path, dict[str, Any]]]:
        if not self.revision_dir.exists():
            return []
        candidates: list[tuple[int, float, Path, dict[str, Any]]] = []
        for path in self.revision_dir.glob("revision-*.toml"):
            if not REVISION_FILE_RE.fullmatch(path.name):
                continue
            try:
                document = _read_document(path)
                revision = int(document["revision"])
                filename_revision = int(REVISION_FILE_RE.fullmatch(path.name).group(1))  # type: ignore[union-attr]
                if revision != filename_revision:
                    continue
                mtime = path.stat().st_mtime
            except (OSError, RegistryError, ValueError):
                continue
            candidates.append((revision, mtime, path, document))
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2])), reverse=True)
        return candidates

    def _latest_revision(self) -> dict[str, Any] | None:
        candidates = self._revision_candidates()
        return copy.deepcopy(candidates[0][3]) if candidates else None

    def _load_locked(self, *, repair: bool = True) -> dict[str, Any]:
        current_error: RegistryError | None = None
        if self.registry_file.exists():
            try:
                return _read_document(self.registry_file)
            except RegistryError as exc:
                current_error = exc

        recovered = self._latest_revision()
        if recovered is not None:
            if repair:
                write_toml(self.registry_file, recovered)
            return recovered
        if current_error is not None:
            raise RegistryError(
                f"registry is invalid and no valid revision is available: {current_error}"
            ) from current_error
        return empty_registry()

    def _write_revision(self, document: Mapping[str, Any]) -> None:
        revision = int(document["revision"])
        if revision <= 0:
            return
        self.revision_dir.mkdir(parents=True, exist_ok=True)
        write_toml(self._revision_path(revision), dict(document))

    def _commit_locked(
        self,
        current: Mapping[str, Any],
        next_document: Mapping[str, Any],
    ) -> dict[str, Any]:
        current_document = _normalize_document(current)
        document = _normalize_document(next_document)
        document["schema_version"] = SCHEMA_VERSION
        document["revision"] = int(current_document["revision"]) + 1
        document["updated_at"] = utc_now()

        # 先保存旧 revision，再替换当前配置；新 revision 也保存一份，保证
        # 只有一次写入时当前文件损坏仍然可以自动恢复。
        self._write_revision(current_document)
        write_toml(self.registry_file, document)
        self._write_revision(document)
        return copy.deepcopy(document)

    def load_locked(self, *, repair: bool = True) -> dict[str, Any]:
        return copy.deepcopy(self._load_locked(repair=repair))

    def save_locked(self, document: Mapping[str, Any]) -> dict[str, Any]:
        current = self._load_locked()
        return copy.deepcopy(self._commit_locked(current, document))

    def load(self, *, repair: bool = True) -> dict[str, Any]:
        with lock_files(self.registry_file):
            return self.load_locked(repair=repair)

    def save(self, document: Mapping[str, Any]) -> dict[str, Any]:
        with lock_files(self.registry_file):
            return self.save_locked(document)

    def get(self, kind: str, object_id: str) -> dict[str, Any] | None:
        normalized_kind = normalize_kind(kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        document = self.load()
        value = document[collection_for(normalized_kind)].get(normalized_id)
        return copy.deepcopy(value) if value is not None else None

    def list(self, kind: str) -> list[dict[str, Any]]:
        normalized_kind = normalize_kind(kind)
        document = self.load()
        objects = document[collection_for(normalized_kind)]
        return [copy.deepcopy(objects[key]) for key in sorted(objects)]

    def _prepare_object(
        self,
        kind: str,
        object_id: str,
        fields: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_kind = normalize_kind(kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        obj = _copy_mapping(fields or {}, f"{normalized_kind} {normalized_id}")
        declared_id = obj.get("id")
        if declared_id not in (None, normalized_id):
            raise RegistryError(f"{normalized_kind} id cannot be changed")
        obj["id"] = normalized_id
        if normalized_kind == "deployment":
            project_id, service_id, environment = normalized_id.split("/")
            expected = {
                "project_id": project_id,
                "service_id": service_id,
                "environment": environment,
            }
            for field, value in expected.items():
                if field in obj and obj[field] != value:
                    raise RegistryError(f"deployment {field} does not match its id")
                obj[field] = value
        return obj

    def create(
        self,
        kind: str,
        object_id: str,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_kind = normalize_kind(kind)
        collection = collection_for(normalized_kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        with lock_files(self.registry_file):
            current = self._load_locked()
            if normalized_id in current[collection]:
                raise RegistryError(f"{normalized_kind} already exists: {normalized_id}")
            next_document = copy.deepcopy(current)
            next_document[collection][normalized_id] = self._prepare_object(
                normalized_kind, normalized_id, fields
            )
            committed = self._commit_locked(current, next_document)
            return copy.deepcopy(committed[collection][normalized_id])

    def update(
        self,
        kind: str,
        object_id: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_kind = normalize_kind(kind)
        collection = collection_for(normalized_kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        with lock_files(self.registry_file):
            current = self._load_locked()
            existing = current[collection].get(normalized_id)
            if existing is None:
                raise RegistryError(f"{normalized_kind} does not exist: {normalized_id}")
            merged = copy.deepcopy(existing)
            merged.update(_copy_mapping(fields, f"{normalized_kind} {normalized_id}"))
            next_document = copy.deepcopy(current)
            next_document[collection][normalized_id] = self._prepare_object(
                normalized_kind, normalized_id, merged
            )
            committed = self._commit_locked(current, next_document)
            return copy.deepcopy(committed[collection][normalized_id])

    def upsert(
        self,
        kind: str,
        object_id: str,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_kind = normalize_kind(kind)
        collection = collection_for(normalized_kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        with lock_files(self.registry_file):
            current = self._load_locked()
            next_document = copy.deepcopy(current)
            if normalized_id in current[collection]:
                merged = copy.deepcopy(current[collection][normalized_id])
                merged.update(_copy_mapping(fields or {}, f"{normalized_kind} {normalized_id}"))
                next_document[collection][normalized_id] = self._prepare_object(
                    normalized_kind, normalized_id, merged
                )
            else:
                next_document[collection][normalized_id] = self._prepare_object(
                    normalized_kind, normalized_id, fields
                )
            committed = self._commit_locked(current, next_document)
            return copy.deepcopy(committed[collection][normalized_id])

    def delete(self, kind: str, object_id: str) -> bool:
        normalized_kind = normalize_kind(kind)
        collection = collection_for(normalized_kind)
        normalized_id = validate_object_id(normalized_kind, object_id)
        with lock_files(self.registry_file):
            current = self._load_locked()
            if normalized_id not in current[collection]:
                return False
            next_document = copy.deepcopy(current)
            del next_document[collection][normalized_id]
            self._commit_locked(current, next_document)
            return True


def _reference_values(value: Any) -> set[str]:
    if value is None:
        return set()
    values = value if isinstance(value, list) else [value]
    return {str(item).strip() for item in values if str(item).strip()}


def _matches_reference(
    item: Mapping[str, Any],
    fields: tuple[str, ...],
    candidates: set[str],
) -> bool:
    if not candidates:
        return False
    return any(_reference_values(item.get(field)) & candidates for field in fields)


def _registry_object(collection: str, object_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": object_id, **copy.deepcopy(dict(item))}


def reference_impact(
    document: Mapping[str, Any],
    *,
    ssh_aliases: set[str] | None = None,
    ssh_profiles: set[str] | None = None,
    db_sources: set[str] | None = None,
    db_profiles: set[str] | None = None,
) -> dict[str, Any]:
    """返回 SSH/数据库引用影响，不修改注册表。

    deployment 和 backup_asset 是实际依赖对象；service 通过项目和服务 ID
    反查，便于删除前向用户展示完整影响面。这里不触碰旧 ssh.toml、
    databases.toml 或密码。
    """

    ssh_values = {str(item).strip() for item in (ssh_aliases or set()) if str(item).strip()}
    ssh_values.update(str(item).strip() for item in (ssh_profiles or set()) if str(item).strip())
    db_values = {str(item).strip() for item in (db_sources or set()) if str(item).strip()}
    db_values.update(str(item).strip() for item in (db_profiles or set()) if str(item).strip())

    deployments: list[dict[str, Any]] = []
    backup_assets: list[dict[str, Any]] = []
    for object_id, item in document.get("deployments", {}).items():
        if not isinstance(item, Mapping):
            continue
        ssh_match = _matches_reference(item, ("ssh_alias", "ssh_profile"), ssh_values)
        db_match = _matches_reference(item, ("db_source", "db_profile", "db_sources"), db_values)
        if ssh_match or db_match:
            deployments.append(_registry_object("deployments", str(object_id), item))
    for object_id, item in document.get("backup_assets", {}).items():
        if not isinstance(item, Mapping):
            continue
        ssh_match = _matches_reference(item, ("ssh_alias", "ssh_profile"), ssh_values)
        db_match = _matches_reference(item, ("db_source", "db_profile", "db_sources"), db_values)
        if ssh_match or db_match:
            backup_assets.append(_registry_object("backup_assets", str(object_id), item))

    service_keys: set[tuple[str, str]] = set()
    for item in [*deployments, *backup_assets]:
        project_id = str(item.get("project_id") or "")
        service_id = str(item.get("service_id") or "")
        if service_id:
            service_keys.add((project_id, service_id))
    services: list[dict[str, Any]] = []
    for object_id, item in document.get("services", {}).items():
        if not isinstance(item, Mapping):
            continue
        key = (str(item.get("project_id") or ""), str(object_id))
        if key in service_keys or ("", str(object_id)) in service_keys:
            services.append(_registry_object("services", str(object_id), item))

    relations: list[dict[str, Any]] = []
    relation_kinds = {
        "ssh_alias", "ssh_profile", "db_source", "db_profile",
        "datasource", "database_source", "database_profile",
    }
    for object_id, item in document.get("relations", {}).items():
        if not isinstance(item, Mapping):
            continue
        source_kind = str(item.get("source_kind") or "").strip().lower()
        target_kind = str(item.get("target_kind") or "").strip().lower()
        source_id = str(item.get("source_id") or "").strip()
        target_id = str(item.get("target_id") or "").strip()
        source_match = source_kind in relation_kinds and (source_id in ssh_values or source_id in db_values)
        target_match = target_kind in relation_kinds and (target_id in ssh_values or target_id in db_values)
        if source_match or target_match:
            relations.append(_registry_object("relations", str(object_id), item))

    for objects in (deployments, services, backup_assets, relations):
        objects.sort(key=lambda item: str(item.get("id", "")))
    impact = {
        "deployments": deployments,
        "services": services,
        "backup_assets": backup_assets,
        "relations": relations,
    }
    impact["counts"] = {
        kind: len(items)
        for kind, items in impact.items()
        if kind in {"deployments", "services", "backup_assets", "relations"}
    }
    impact["has_references"] = any(impact["counts"].values())
    return impact


def synchronize_references(
    document: dict[str, Any],
    replacements: Mapping[str, str],
    fields_by_collection: Mapping[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    """将仍指向同一 profile 的旧 alias/source 改为存活的别名。"""

    changes: list[dict[str, Any]] = []
    for collection, fields in fields_by_collection.items():
        objects = document.get(collection, {})
        if not isinstance(objects, Mapping):
            continue
        for object_id, raw_item in objects.items():
            if not isinstance(raw_item, dict):
                continue
            for field in fields:
                if field not in raw_item:
                    continue
                original = raw_item[field]
                if isinstance(original, list):
                    updated = [replacements.get(str(value), value) for value in original]
                    if updated == original:
                        continue
                else:
                    updated = replacements.get(str(original), original)
                    if updated == original:
                        continue
                raw_item[field] = updated
                changes.append(
                    {
                        "kind": COLLECTION_TO_KIND.get(collection, collection),
                        "id": str(object_id),
                        "field": field,
                        "from": copy.deepcopy(original),
                        "to": copy.deepcopy(updated),
                    }
                )
    return changes


def mark_references_orphaned(
    document: dict[str, Any],
    impact: Mapping[str, Any],
    reason: str,
) -> list[dict[str, Any]]:
    """把依赖外部连接身份的 deployment/backup_asset 标记为 orphaned。"""

    marked: list[dict[str, Any]] = []
    for collection in ("deployments", "backup_assets", "relations"):
        objects = document.get(collection, {})
        impacted = impact.get(collection, [])
        if not isinstance(objects, dict) or not isinstance(impacted, list):
            continue
        for item in impacted:
            if not isinstance(item, Mapping):
                continue
            object_id = str(item.get("id") or "")
            stored = objects.get(object_id)
            if not isinstance(stored, dict):
                continue
            stored["status"] = "orphaned"
            if collection == "deployments":
                stored["management_status"] = "orphaned"
            stored["orphaned_at"] = utc_now()
            reasons = _reference_values(stored.get("orphaned_reasons"))
            reasons.add(reason)
            stored["orphaned_reasons"] = sorted(reasons)
            marked.append(
                {
                    "kind": COLLECTION_TO_KIND[collection],
                    "id": object_id,
                    "status": "orphaned",
                }
            )
    marked.sort(key=lambda item: (item["kind"], item["id"]))
    return marked
