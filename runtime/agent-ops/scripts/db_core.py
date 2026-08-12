#!/usr/bin/env python3
"""Multi-database SQL utility for MySQL/MariaDB/PostgreSQL."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse


IGNORED_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    ".svn",
    ".hg",
    ".next",
    ".nuxt",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    "__pycache__",
}

ENV_FILE_NAMES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
)

APP_FILE_RE = re.compile(
    r"^application(?:[-_.].+)?\.(?:yml|yaml|properties)$", re.IGNORECASE
)
PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")
DYNAMIC_URL_RE = re.compile(r"^spring\.datasource\.dynamic\.datasource\.([^.]+)\.url$")

READ_ONLY_QUERY_KEYWORDS = {"select", "with", "show", "describe", "desc", "explain"}
CURRENT_OUTPUT = "model"
SAFETY_RULES = {
    "drop": re.compile(
        r"\bdrop\s+(database|schema|table|view|index|sequence|function|procedure|trigger)\b",
        re.IGNORECASE,
    ),
    "truncate": re.compile(r"\btruncate\b", re.IGNORECASE),
    "delete": re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    "alter_drop": re.compile(
        r"\balter\s+table\b[\s\S]*?\bdrop\s+(column|constraint)\b", re.IGNORECASE
    ),
    "grant_revoke": re.compile(r"\b(grant|revoke)\b", re.IGNORECASE),
}


@dataclass
class DataSource:
    """Normalized datasource settings."""

    engine: str
    host: str
    port: int
    database: str
    user: str
    password: str
    source: str
    framework: str
    profile: str = ""
    name: str = "default"
    url: str = ""

    def masked(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["password"] = "***" if self.password else ""
        if payload.get("url"):
            payload["url"] = mask_secret_in_url(payload["url"])
        return payload


def to_json_value(value: Any) -> Any:
    """Convert database driver values to JSON-native values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_value(item) for item in value]
    return str(value)


def print_json(payload: Dict[str, Any]) -> None:
    """Print deterministic JSON output for downstream automation."""

    if CURRENT_OUTPUT == "full":
        text = json.dumps(to_json_value(payload), ensure_ascii=False, indent=2)
    elif CURRENT_OUTPUT == "compact":
        text = json.dumps(
            to_json_value(payload), ensure_ascii=False, separators=(",", ":")
        )
    else:
        text = dumps_model_json(to_json_value(payload))
    sys.stdout.write(text)
    sys.stdout.write("\n")


def dumps_model_json(value: Any, level: int = 0) -> str:
    """Pretty JSON that keeps scalar arrays on one line for query rows."""

    indent = " " * level
    child_indent = " " * (level + 2)

    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            comma = "," if index < len(items) - 1 else ""
            key_text = json.dumps(str(key), ensure_ascii=False)
            lines.append(
                f"{child_indent}{key_text}: {dumps_model_json(item, level + 2)}{comma}"
            )
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    if isinstance(value, list):
        if not value:
            return "[]"
        if all(is_scalar_json(item) for item in value):
            return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        lines = ["["]
        for index, item in enumerate(value):
            comma = "," if index < len(value) - 1 else ""
            lines.append(f"{child_indent}{dumps_model_json(item, level + 2)}{comma}")
        lines.append(f"{indent}]")
        return "\n".join(lines)

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_scalar_json(value: Any) -> bool:
    """Return true for JSON values that are readable inline."""

    return value is None or isinstance(value, (str, bool, int, float))


def fail(message: str, details: Optional[Dict[str, Any]] = None, code: int = 1) -> None:
    """Print error payload and exit."""

    payload = {"ok": False, "error": message}
    if details:
        if CURRENT_OUTPUT == "full":
            payload["details"] = details
        elif details.get("hint"):
            payload["hint"] = details["hint"]
    print_json(payload)
    raise SystemExit(code)


def datasource_brief(datasource: DataSource) -> str:
    """Build a compact datasource label for routine command output."""

    return (
        f"{datasource.engine} {datasource.database}@"
        f"{datasource.host}:{datasource.port}"
    )


def compact_datasource(datasource: DataSource) -> Dict[str, Any]:
    """Return compact datasource details for discovery output."""

    return {
        "db": datasource_brief(datasource),
        "name": datasource.name,
        "profile": datasource.profile,
        "source": datasource.source,
    }


def model_datasource(datasource: DataSource) -> Dict[str, Any]:
    """Return model-friendly datasource details without secrets."""

    return {
        "engine": datasource.engine,
        "database": datasource.database,
        "host": datasource.host,
        "port": datasource.port,
        "name": datasource.name,
        "profile": datasource.profile,
        "source": datasource.source,
    }


def build_output_payload(
    action: str,
    framework: str,
    candidates: List[DataSource],
    datasource: DataSource,
    result: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build compact or full command output."""

    if CURRENT_OUTPUT == "full":
        payload: Dict[str, Any] = {
            "ok": True,
            "action": action,
            "framework": framework,
            "candidate_count": len(candidates),
            "datasource": datasource.masked(),
            "result": result,
        }
    elif CURRENT_OUTPUT == "model":
        summary: Dict[str, Any] = {
            "engine": datasource.engine,
            "database": datasource.database,
        }
        if action == "query":
            summary["returned_rows"] = result.get("returned_rows", 0)
            summary["has_more"] = result.get("has_more", result.get("truncated", False))
        payload = {
            "ok": True,
            "action": action,
            "db": datasource_brief(datasource),
            "summary": summary,
            "result": result,
        }
    else:
        payload = {
            "ok": True,
            "action": action,
            "db": datasource_brief(datasource),
            "result": result,
        }

    if extra:
        payload.update(extra)
    return payload


def normalize_engine(engine: str) -> str:
    """Normalize engine aliases to canonical values."""

    lowered = engine.strip().lower()
    if lowered in {"postgres", "postgresql"}:
        return "postgresql"
    if lowered in {"mysql", "mariadb"}:
        return lowered
    raise ValueError(f"Unsupported database engine: {engine}")


def normalize_framework(framework: str) -> str:
    """Normalize framework selector for datasource discovery."""

    lowered = framework.strip().lower()
    mapping = {
        "auto": "auto",
        "spring": "spring",
        "springboot": "spring",
        "node": "node",
        "nodejs": "node",
        "python": "python",
        "manual": "manual",
    }
    return mapping.get(lowered, "auto")


def default_port(engine: str) -> int:
    """Return default port by engine."""

    if engine in {"mysql", "mariadb"}:
        return 3306
    if engine == "postgresql":
        return 5432
    raise ValueError(f"Unsupported database engine: {engine}")


def mask_secret_in_url(url: str) -> str:
    """Mask credential segment in URL-like strings."""

    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url, count=1)


def strip_quotes(value: str) -> str:
    """Strip optional single or double quotes from a value."""

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def resolve_placeholders(value: str) -> str:
    """Resolve placeholders like ${KEY} and ${KEY:default}."""

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        fallback = match.group(2) or ""
        return os.getenv(key, fallback)

    return PLACEHOLDER_RE.sub(replacer, value)


def parse_jdbc_url(url: str) -> Optional[Dict[str, Any]]:
    """Parse JDBC URLs for mysql/mariadb/postgresql."""

    text = resolve_placeholders(url.strip())
    match = re.match(
        r"^jdbc:(mysql|mariadb|postgresql)://(\[[^\]]+]|[^:/?#]+)(?::(\d+))?/([^?;]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    engine = normalize_engine(match.group(1))
    host = match.group(2)
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    port = int(match.group(3)) if match.group(3) else default_port(engine)
    database = match.group(4).strip()

    return {
        "engine": engine,
        "host": host,
        "port": port,
        "database": database,
        "user": "",
        "password": "",
        "url": text,
    }


def parse_standard_url(url: str) -> Optional[Dict[str, Any]]:
    """Parse mysql/mariadb/postgresql URL formats."""

    text = resolve_placeholders(url.strip())
    if text.startswith("jdbc:"):
        return parse_jdbc_url(text)

    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"mysql", "mariadb", "postgres", "postgresql"}:
        return None

    engine = normalize_engine(scheme)
    database = parsed.path.lstrip("/").split("/", 1)[0]
    if not database:
        return None

    return {
        "engine": engine,
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or default_port(engine),
        "database": database,
        "user": unquote(parsed.username) if parsed.username else "",
        "password": unquote(parsed.password) if parsed.password else "",
        "url": text,
    }


def parse_connection_url(url: str) -> Optional[Dict[str, Any]]:
    """Parse JDBC or standard database URL."""

    return parse_standard_url(url)


def parse_properties_file(path: Path) -> Dict[str, str]:
    """Parse Java .properties file as flat key-value pairs."""

    data: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue

        separator = "=" if "=" in line else ":" if ":" in line else None
        if separator is None:
            continue

        key, value = line.split(separator, 1)
        key = key.strip()
        value = resolve_placeholders(strip_quotes(value.strip()))
        data[key] = value

    return data


def parse_simple_yaml_file(path: Path) -> Dict[str, str]:
    """Parse simple YAML into flattened dotted keys."""

    data: Dict[str, str] = {}
    stack: List[Tuple[int, str]] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.rstrip()
        if not stripped:
            continue

        pure = stripped.split(" #", 1)[0].rstrip()
        if not pure:
            continue

        content = pure.lstrip()
        if content.startswith("#") or content.startswith("- "):
            continue
        if ":" not in content:
            continue

        indent = len(pure) - len(content)
        key, value = content.split(":", 1)
        key = strip_quotes(key.strip())
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()

        if value == "":
            stack.append((indent, key))
            continue

        full_key = ".".join([item[1] for item in stack] + [key])
        data[full_key] = resolve_placeholders(strip_quotes(value))

    return data


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse .env style key-value file."""

    data: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {'"', "'"}:
            value = strip_quotes(value)
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        data[key] = resolve_placeholders(value)

    return data


def walk_project_files(root: Path) -> Iterable[Tuple[Path, List[str], List[str]]]:
    """Yield project files while skipping heavy irrelevant directories."""

    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in IGNORED_DIR_NAMES]
        yield Path(current_root), dir_names, file_names


def find_application_files(project_root: Path) -> List[Path]:
    """Find Spring application config files recursively."""

    files: List[Path] = []
    for current_root, _, file_names in walk_project_files(project_root):
        for file_name in file_names:
            if APP_FILE_RE.match(file_name):
                files.append(current_root / file_name)

    files.sort(key=lambda item: (len(item.relative_to(project_root).parts), str(item)))
    return files


def find_env_files(project_root: Path, max_depth: int = 3) -> List[Path]:
    """Find .env files up to a limited directory depth."""

    files: List[Path] = []
    for current_root, dir_names, file_names in walk_project_files(project_root):
        rel_depth = len(current_root.relative_to(project_root).parts)
        if rel_depth >= max_depth:
            dir_names[:] = []
        for file_name in file_names:
            if file_name in ENV_FILE_NAMES:
                files.append(current_root / file_name)

    files.sort(key=lambda item: (len(item.relative_to(project_root).parts), str(item)))
    return files


def infer_profile(file_name: str) -> str:
    """Infer Spring profile from application file name."""

    matched = re.match(
        r"^application[-_.]([^.]+)\.(?:yml|yaml|properties)$", file_name, re.IGNORECASE
    )
    return matched.group(1) if matched else ""


def datasource_from_parts(
    parsed_url: Dict[str, Any],
    user: str,
    password: str,
    source: str,
    framework: str,
    profile: str = "",
    name: str = "default",
) -> DataSource:
    """Create DataSource from URL parts plus auth fields."""

    merged_user = user or parsed_url.get("user", "")
    merged_password = password or parsed_url.get("password", "")
    return DataSource(
        engine=normalize_engine(parsed_url["engine"]),
        host=parsed_url["host"],
        port=int(parsed_url["port"]),
        database=parsed_url["database"],
        user=merged_user,
        password=merged_password,
        source=source,
        framework=framework,
        profile=profile,
        name=name,
        url=parsed_url.get("url", ""),
    )


def extract_spring_candidates(
    config: Dict[str, str], source_file: Path
) -> List[DataSource]:
    """Extract datasource candidates from parsed Spring config."""

    candidates: List[DataSource] = []
    profile = infer_profile(source_file.name)

    dynamic_primary = config.get("spring.datasource.dynamic.primary", "").strip()
    for key, url in config.items():
        match = DYNAMIC_URL_RE.match(key)
        if not match:
            continue

        datasource_name = match.group(1)
        parsed_url = parse_connection_url(url)
        if not parsed_url:
            continue

        prefix = f"spring.datasource.dynamic.datasource.{datasource_name}."
        user = config.get(prefix + "username", "")
        password = config.get(prefix + "password", "")
        alias = datasource_name
        if dynamic_primary and datasource_name == dynamic_primary:
            alias = "primary"

        candidates.append(
            datasource_from_parts(
                parsed_url=parsed_url,
                user=user,
                password=password,
                source=f"{source_file}#{datasource_name}",
                framework="spring",
                profile=profile,
                name=alias,
            )
        )

    default_url = config.get("spring.datasource.url") or config.get(
        "spring.datasource.hikari.jdbc-url"
    )
    if default_url:
        parsed_default = parse_connection_url(default_url)
        if parsed_default:
            user = config.get("spring.datasource.username", "") or config.get(
                "spring.datasource.hikari.username", ""
            )
            password = config.get("spring.datasource.password", "") or config.get(
                "spring.datasource.hikari.password", ""
            )
            candidates.append(
                datasource_from_parts(
                    parsed_url=parsed_default,
                    user=user,
                    password=password,
                    source=str(source_file),
                    framework="spring",
                    profile=profile,
                    name="default",
                )
            )

    return candidates


def discover_spring_datasources(
    project_root: Path, config_file: str = ""
) -> List[DataSource]:
    """Discover datasource candidates from application*.yml/properties files."""

    if config_file:
        candidate_path = Path(config_file)
        if not candidate_path.is_absolute():
            candidate_path = (project_root / candidate_path).resolve()
        files = [candidate_path]
    else:
        files = find_application_files(project_root)

    candidates: List[DataSource] = []
    for file_path in files:
        if not file_path.exists() or not file_path.is_file():
            continue

        suffix = file_path.suffix.lower()
        if suffix == ".properties":
            config = parse_properties_file(file_path)
        else:
            config = parse_simple_yaml_file(file_path)
        candidates.extend(extract_spring_candidates(config, file_path))

    return candidates


def discover_env_datasources(project_root: Path, framework: str) -> List[DataSource]:
    """Discover datasource candidates from .env* files."""

    candidates: List[DataSource] = []
    env_keys = (
        "DATABASE_URL",
        "DB_URL",
        "MYSQL_URL",
        "MARIADB_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    )

    for file_path in find_env_files(project_root):
        data = parse_env_file(file_path)
        for key in env_keys:
            raw_url = data.get(key, "")
            if not raw_url:
                continue
            parsed = parse_connection_url(raw_url)
            if not parsed:
                continue
            candidates.append(
                datasource_from_parts(
                    parsed_url=parsed,
                    user=parsed.get("user", ""),
                    password=parsed.get("password", ""),
                    source=f"{file_path}#{key}",
                    framework=framework,
                    name=key,
                )
            )

    return candidates


def discover_process_env_datasources() -> List[DataSource]:
    """Discover datasource candidates from process environment variables."""

    candidates: List[DataSource] = []
    env_keys = (
        "DATABASE_URL",
        "DB_URL",
        "MYSQL_URL",
        "MARIADB_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    )
    for key in env_keys:
        raw_url = os.getenv(key, "").strip()
        if not raw_url:
            continue
        parsed = parse_connection_url(raw_url)
        if not parsed:
            continue
        candidates.append(
            datasource_from_parts(
                parsed_url=parsed,
                user=parsed.get("user", ""),
                password=parsed.get("password", ""),
                source=f"process-env:{key}",
                framework="env",
                name=key,
            )
        )
    return candidates


def dedupe_candidates(candidates: List[DataSource]) -> List[DataSource]:
    """Dedupe candidates while preserving first-seen order."""

    seen = set()
    unique: List[DataSource] = []
    for item in candidates:
        marker = (
            item.engine,
            item.host,
            item.port,
            item.database,
            item.user,
            item.password,
        )
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def build_manual_datasource(args: argparse.Namespace) -> Optional[DataSource]:
    """Build datasource from explicit user input when provided."""

    if args.url:
        parsed = parse_connection_url(args.url)
        if not parsed:
            fail("Failed to parse provided --url", {"url": args.url})
        assert parsed is not None

        engine = normalize_engine(args.db_type) if args.db_type else parsed["engine"]
        host = args.host or parsed["host"]
        port = args.port or parsed["port"] or default_port(engine)
        database = args.database or parsed["database"]
        user = args.user or parsed.get("user", "")
        password = args.password or parsed.get("password", "")

        return DataSource(
            engine=engine,
            host=host,
            port=int(port),
            database=database,
            user=user,
            password=password,
            source="manual:url",
            framework="manual",
            name=args.name or "manual",
            url=args.url,
        )

    if args.db_type or args.host or args.database or args.user:
        if not args.db_type:
            fail("Missing --db-type for manual datasource input")
        if not args.database:
            fail("Missing --database for manual datasource input")
        if not args.user:
            fail("Missing --user for manual datasource input")

        engine = normalize_engine(args.db_type)
        return DataSource(
            engine=engine,
            host=args.host or "127.0.0.1",
            port=args.port or default_port(engine),
            database=args.database,
            user=args.user,
            password=args.password or "",
            source="manual:fields",
            framework="manual",
            name=args.name or "manual",
            url="",
        )

    return None


def apply_overrides(datasource: DataSource, args: argparse.Namespace) -> DataSource:
    """Apply optional user overrides to selected datasource."""

    updated = datasource
    if args.db_type:
        updated = replace(updated, engine=normalize_engine(args.db_type))
    if args.host:
        updated = replace(updated, host=args.host)
    if args.port:
        updated = replace(updated, port=int(args.port))
    if args.database:
        updated = replace(updated, database=args.database)
    if args.user:
        updated = replace(updated, user=args.user)
    if args.password:
        updated = replace(updated, password=args.password)
    return updated


def pick_candidate(candidates: List[DataSource], profile: str) -> DataSource:
    """Pick one datasource candidate using profile and defaults."""

    if not candidates:
        raise ValueError("No datasource candidates found")

    if profile:
        profiled = [item for item in candidates if item.profile == profile]
        if profiled:
            return profiled[0]

    active_profile = os.getenv("SPRING_PROFILES_ACTIVE", "").strip()
    if active_profile:
        profiled = [item for item in candidates if item.profile == active_profile]
        if profiled:
            return profiled[0]

    no_profile = [item for item in candidates if not item.profile]
    if no_profile:
        return no_profile[0]
    return candidates[0]


def collect_candidates(args: argparse.Namespace) -> Tuple[List[DataSource], str]:
    """Collect datasource candidates based on manual inputs or discovery."""

    manual = build_manual_datasource(args)
    if manual:
        return [manual], "manual"

    project_root = Path(args.project_root).resolve()
    framework = normalize_framework(args.framework)

    if framework == "manual":
        fail("Framework is manual but no explicit datasource args were provided")

    candidates: List[DataSource] = []
    if framework in {"auto", "spring"}:
        candidates.extend(
            discover_spring_datasources(project_root, args.config_file or "")
        )

    if framework in {"auto", "node", "python"}:
        env_framework = framework if framework in {"node", "python"} else "env"
        candidates.extend(discover_env_datasources(project_root, env_framework))

    candidates.extend(discover_process_env_datasources())
    candidates = dedupe_candidates(candidates)

    if args.db_type:
        expected = normalize_engine(args.db_type)
        candidates = [item for item in candidates if item.engine == expected]

    return candidates, framework


def resolve_datasource(
    args: argparse.Namespace,
) -> Tuple[DataSource, List[DataSource], str]:
    """Resolve one datasource for execution and return candidate context."""

    candidates, framework = collect_candidates(args)
    if not candidates:
        fail(
            "No datasource found. Provide explicit args or ensure project config includes datasource settings.",
            {
                "hint": "Check application*.yml/properties or .env* and ensure URL format is valid.",
                "project_root": str(Path(args.project_root).resolve()),
            },
        )

    selected = pick_candidate(candidates, args.profile or "")
    selected = apply_overrides(selected, args)

    if not selected.host or not selected.database or not selected.user:
        fail(
            "Datasource is incomplete",
            {
                "missing": {
                    "host": bool(selected.host),
                    "database": bool(selected.database),
                    "user": bool(selected.user),
                },
                "source": selected.source,
            },
        )

    return selected, candidates, framework


def connect(datasource: DataSource) -> Tuple[str, Any]:
    """Create database connection for selected engine."""

    if datasource.engine in {"mysql", "mariadb"}:
        try:
            import pymysql  # type: ignore
            from pymysql.constants import CLIENT  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency pymysql. Install with: python3 -m pip install pymysql"
            ) from exc

        connection = pymysql.connect(
            host=datasource.host,
            port=int(datasource.port),
            user=datasource.user,
            password=datasource.password,
            database=datasource.database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
            client_flag=CLIENT.MULTI_STATEMENTS,
        )
        return "pymysql", connection

    if datasource.engine == "postgresql":
        try:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore

            connection = psycopg.connect(
                host=datasource.host,
                port=int(datasource.port),
                user=datasource.user,
                password=datasource.password,
                dbname=datasource.database,
                autocommit=False,
                row_factory=dict_row,
            )
            return "psycopg", connection
        except ImportError:
            try:
                import psycopg2  # type: ignore

                connection = psycopg2.connect(
                    host=datasource.host,
                    port=int(datasource.port),
                    user=datasource.user,
                    password=datasource.password,
                    dbname=datasource.database,
                )
                return "psycopg2", connection
            except ImportError as exc:
                raise RuntimeError(
                    "Missing PostgreSQL driver. Install with: python3 -m pip install psycopg[binary] or psycopg2-binary"
                ) from exc

    raise RuntimeError(f"Unsupported engine: {datasource.engine}")


def load_sql_text(sql: str, sql_file: str) -> str:
    """Load SQL from inline text or file path."""

    if sql and sql.strip():
        return sql.strip()

    if sql_file:
        path = Path(sql_file).resolve()
        if not path.exists() or not path.is_file():
            fail("SQL file does not exist", {"sql_file": str(path)})
        return path.read_text(encoding="utf-8").strip()

    fail("Provide --sql or --sql-file")
    return ""


def strip_sql_comments(sql: str) -> str:
    """Remove simple SQL comments before safety checks."""

    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    lines = []
    for line in without_block.splitlines():
        line = re.sub(r"--.*$", "", line)
        line = re.sub(r"#.*$", "", line)
        lines.append(line)
    return "\n".join(lines)


def split_sql_statements(sql: str) -> List[str]:
    """Split SQL text by semicolon while handling simple quoted strings."""

    statements: List[str] = []
    current: List[str] = []
    in_single = False
    in_double = False
    in_backtick = False

    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if in_single:
            current.append(char)
            if char == "'":
                if next_char == "'":
                    current.append(next_char)
                    index += 1
                else:
                    in_single = False
        elif in_double:
            current.append(char)
            if char == '"':
                in_double = False
        elif in_backtick:
            current.append(char)
            if char == "`":
                in_backtick = False
        else:
            if char == "'":
                in_single = True
                current.append(char)
            elif char == '"':
                in_double = True
                current.append(char)
            elif char == "`":
                in_backtick = True
                current.append(char)
            elif char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
            else:
                current.append(char)

        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def detect_dangerous_patterns(sql: str) -> List[str]:
    """Detect risky SQL patterns under default safety mode."""

    normalized_sql = strip_sql_comments(sql)
    hits: List[str] = []
    for rule_name, rule in SAFETY_RULES.items():
        if rule.search(normalized_sql):
            hits.append(rule_name)
    return hits


def enforce_sql_safety(sql: str, allow_dangerous: bool, action: str) -> None:
    """Block high-risk SQL unless user explicitly allows it."""

    if allow_dangerous:
        return

    hits = detect_dangerous_patterns(sql)
    if hits:
        fail(
            "Safety mode blocked potentially destructive SQL",
            {
                "action": action,
                "safety_mode": "on",
                "matched_rules": hits,
                "hint": "Use --allow-dangerous to bypass this guard when you are sure.",
            },
        )


def enforce_query_read_only(sql: str, allow_dangerous: bool) -> None:
    """Require read-only statements in query mode by default."""

    statements = split_sql_statements(strip_sql_comments(sql))
    if not statements:
        fail("No executable SQL statement found")

    blocked_keywords: List[str] = []
    for statement in statements:
        match = re.match(r"^\s*([a-zA-Z]+)", statement)
        keyword = match.group(1).lower() if match else ""
        if keyword not in READ_ONLY_QUERY_KEYWORDS:
            blocked_keywords.append(keyword or "unknown")

    if blocked_keywords and not allow_dangerous:
        fail(
            "Query mode only allows read-only SQL by default",
            {
                "blocked_keywords": blocked_keywords,
                "hint": "Use --allow-dangerous to run non-read-only SQL in query mode.",
            },
        )

    enforce_sql_safety(sql, allow_dangerous, action="query")


def dict_rows(cursor: Any) -> List[Dict[str, Any]]:
    """Fetch cursor rows and normalize them to dict entries."""

    rows = cursor.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [dict(row) for row in rows]

    if not cursor.description:
        return []
    columns = [item[0] for item in cursor.description]
    return [
        {columns[index]: row[index] for index in range(len(columns))} for row in rows
    ]


def rows_to_dicts(cursor: Any, fetched: Iterable[Any]) -> List[Dict[str, Any]]:
    """Normalize fetched cursor rows to dict entries."""

    rows = list(fetched)
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [dict(row) for row in rows]

    if not cursor.description:
        return []
    columns = [item[0] for item in cursor.description]
    return [
        {columns[index]: row[index] for index in range(len(columns))} for row in rows
    ]


def cursor_columns(cursor: Any) -> List[str]:
    """Return cursor result column names."""

    if not cursor.description:
        return []
    return [str(item[0]) for item in cursor.description]


def fetch_query_rows(
    cursor: Any, limit: int
) -> Tuple[List[Dict[str, Any]], bool]:
    """Fetch query rows with preview mode support."""

    if limit <= 0:
        rows = rows_to_dicts(cursor, cursor.fetchall())
        return rows, False

    rows = rows_to_dicts(cursor, cursor.fetchmany(limit + 1))
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    return rows, truncated


def truncate_json_cells(value: Any, max_chars: int) -> Tuple[Any, bool]:
    """Limit long compact output values after JSON normalization."""

    normalized = to_json_value(value)
    if max_chars <= 0:
        return normalized, False

    if isinstance(normalized, str):
        if len(normalized) <= max_chars:
            return normalized, False
        return normalized[:max_chars] + "...", True
    if isinstance(normalized, dict):
        result: Dict[str, Any] = {}
        truncated = False
        for key, item in normalized.items():
            result[key], item_truncated = truncate_json_cells(item, max_chars)
            truncated = truncated or item_truncated
        return result, truncated
    if isinstance(normalized, list):
        result_list = []
        truncated = False
        for item in normalized:
            compact_item, item_truncated = truncate_json_cells(item, max_chars)
            result_list.append(compact_item)
            truncated = truncated or item_truncated
        return result_list, truncated
    return normalized, False


def compact_query_result(result: Dict[str, Any], max_cell_chars: int) -> Dict[str, Any]:
    """Build compact query result with bounded cell sizes."""

    rows = []
    cells_truncated = False
    for row in result["rows"]:
        compact_row, row_truncated = truncate_json_cells(row, max_cell_chars)
        rows.append(compact_row)
        cells_truncated = cells_truncated or row_truncated

    compact = {
        "returned_rows": result["returned_rows"],
        "truncated": result["truncated"],
        "rows": rows,
    }
    if cells_truncated:
        compact["cells_truncated"] = True
        compact["max_cell_chars"] = max_cell_chars
    return compact


def model_query_result(
    result: Dict[str, Any],
    max_cell_chars: int,
    row_format: str,
    with_row_number: bool,
) -> Dict[str, Any]:
    """Build query output optimized for LLM reading."""

    columns = list(result.get("columns", []))
    output_columns = (["#"] + columns) if with_row_number else columns
    rows = []
    cells_truncated = False

    for index, row in enumerate(result["rows"], start=1):
        compact_row, row_truncated = truncate_json_cells(row, max_cell_chars)
        cells_truncated = cells_truncated or row_truncated
        if row_format == "object":
            if with_row_number:
                compact_row = {"#": index, **compact_row}
            rows.append(compact_row)
        else:
            values = [compact_row.get(column) for column in columns]
            rows.append(([index] if with_row_number else []) + values)

    model = {
        "columns": output_columns,
        "returned_rows": result["returned_rows"],
        "limit": result["limit"],
        "has_more": result["has_more"],
        "truncated": result["truncated"],
        "rows": rows,
    }
    if cells_truncated:
        model["cells_truncated"] = True
        model["max_cell_chars"] = max_cell_chars
    return model


def parse_table_filter(tables: str, schema: str = "") -> List[str]:
    """Parse comma-separated tables and normalize optional schema prefix."""

    if not tables.strip():
        return []

    result: List[str] = []
    seen = set()
    for raw_item in tables.split(","):
        item = raw_item.strip()
        if not item:
            continue

        if "." in item and schema:
            left, right = item.split(".", 1)
            if left.strip('"`') == schema:
                item = right
        item = item.strip('"`')
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def filter_tables(all_tables: List[str], requested_tables: List[str]) -> List[str]:
    """Filter and validate tables requested by user."""

    if not requested_tables:
        return all_tables

    available = set(all_tables)
    missing = [table for table in requested_tables if table not in available]
    if missing:
        fail(
            "Some requested tables were not found",
            {
                "missing_tables": missing,
                "available_tables": all_tables,
            },
        )

    return [table for table in requested_tables if table in available]


def quote_identifier(name: str, engine: str) -> str:
    """Quote SQL identifiers by engine."""

    if engine in {"mysql", "mariadb"}:
        return f"`{name.replace('`', '``')}`"
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value: Any, engine: str) -> str:
    """Convert Python values to SQL literals."""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return f"'{value.isoformat(sep=' ', timespec='microseconds') if isinstance(value, dt.datetime) else value.isoformat()}'"
    if isinstance(value, (bytes, bytearray)):
        hex_text = bytes(value).hex()
        if engine in {"mysql", "mariadb"}:
            return f"x'{hex_text}'"
        return f"'\\\\x{hex_text}'::bytea"
    if isinstance(value, (dict, list, tuple)):
        escaped_json = json.dumps(value, ensure_ascii=False).replace("'", "''")
        return f"'{escaped_json}'"

    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def dump_insert_rows(
    table_name: str,
    columns: List[str],
    rows: List[Any],
    engine: str,
    schema: str = "",
) -> str:
    """Build one INSERT statement for a batch of rows."""

    if schema:
        target = (
            f"{quote_identifier(schema, engine)}.{quote_identifier(table_name, engine)}"
        )
    else:
        target = quote_identifier(table_name, engine)
    column_sql = ", ".join(quote_identifier(column, engine) for column in columns)

    value_chunks = []
    for row in rows:
        if isinstance(row, dict):
            ordered_values = [row.get(column) for column in columns]
        else:
            ordered_values = list(row)
        literal_values = ", ".join(
            sql_literal(value, engine) for value in ordered_values
        )
        value_chunks.append(f"({literal_values})")

    joined_values = ",\n  ".join(value_chunks)
    return f"INSERT INTO {target} ({column_sql}) VALUES\n  {joined_values};"


def mysql_table_names(cursor: Any) -> List[str]:
    """Fetch all base table names for MySQL/MariaDB."""

    cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
    rows = cursor.fetchall()
    names: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            values = list(row.values())
            if values:
                names.append(str(values[0]))
        else:
            names.append(str(row[0]))
    return sorted(names)


def mysql_schema_statements(
    cursor: Any, table_name: str, include_drop: bool
) -> List[str]:
    """Generate MySQL/MariaDB CREATE TABLE SQL for one table."""

    statements: List[str] = []
    table_ident = quote_identifier(table_name, "mysql")
    if include_drop:
        statements.append(f"DROP TABLE IF EXISTS {table_ident};")

    cursor.execute(f"SHOW CREATE TABLE {table_ident}")
    row = cursor.fetchone()
    if not row:
        fail("Failed to read table schema", {"table": table_name})

    if isinstance(row, dict):
        create_sql = row.get("Create Table") or row.get("Create View")
        if not create_sql:
            values = list(row.values())
            create_sql = values[1] if len(values) > 1 else ""
    else:
        create_sql = row[1] if len(row) > 1 else ""

    if not create_sql:
        fail("Empty table schema returned", {"table": table_name})
    statements.append(f"{create_sql};")
    return statements


def mysql_data_statements(cursor: Any, table_name: str, batch_size: int) -> List[str]:
    """Generate MySQL/MariaDB INSERT statements for one table."""

    table_ident = quote_identifier(table_name, "mysql")
    cursor.execute(f"SELECT * FROM {table_ident}")
    if not cursor.description:
        return []

    columns = [column[0] for column in cursor.description]
    statements: List[str] = []
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        statements.append(
            dump_insert_rows(
                table_name=table_name,
                columns=columns,
                rows=rows,
                engine="mysql",
            )
        )
    return statements


def postgresql_table_names(cursor: Any, schema: str) -> List[str]:
    """Fetch all table names for PostgreSQL schema."""

    cursor.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = %s
        ORDER BY tablename
        """,
        (schema,),
    )
    rows = dict_rows(cursor)
    return [str(row["tablename"]) for row in rows]


def postgresql_schema_statements(
    cursor: Any,
    schema: str,
    table_name: str,
    include_drop: bool,
) -> List[str]:
    """Generate PostgreSQL CREATE TABLE and index statements for one table."""

    cursor.execute(
        """
        SELECT
            a.attname AS column_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
            a.attnotnull AS not_null,
            pg_get_expr(ad.adbin, ad.adrelid) AS default_value
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
        WHERE n.nspname = %s
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (schema, table_name),
    )
    column_rows = dict_rows(cursor)
    if not column_rows:
        fail("Failed to read table columns", {"schema": schema, "table": table_name})

    cursor.execute(
        """
        SELECT
            c.conname,
            c.contype,
            pg_get_constraintdef(c.oid, true) AS constraint_def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s
          AND t.relname = %s
        ORDER BY c.contype, c.conname
        """,
        (schema, table_name),
    )
    constraint_rows = dict_rows(cursor)

    cursor.execute(
        """
        SELECT
            i.relname AS index_name,
            pg_get_indexdef(i.oid) AS index_def
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_index ix ON ix.indrelid = t.oid
        JOIN pg_class i ON i.oid = ix.indexrelid
        LEFT JOIN pg_constraint c ON c.conindid = ix.indexrelid
        WHERE n.nspname = %s
          AND t.relname = %s
          AND c.oid IS NULL
        ORDER BY i.relname
        """,
        (schema, table_name),
    )
    index_rows = dict_rows(cursor)

    full_table = f"{quote_identifier(schema, 'postgresql')}.{quote_identifier(table_name, 'postgresql')}"
    definitions: List[str] = []
    for row in column_rows:
        column_sql = f"{quote_identifier(str(row['column_name']), 'postgresql')} {row['data_type']}"
        if row.get("default_value") is not None:
            column_sql += f" DEFAULT {row['default_value']}"
        if row.get("not_null"):
            column_sql += " NOT NULL"
        definitions.append(column_sql)

    for row in constraint_rows:
        # Skip named NOT NULL constraints because column-level NOT NULL is already emitted.
        if str(row.get("contype", "")) == "n":
            continue
        definitions.append(
            f"CONSTRAINT {quote_identifier(str(row['conname']), 'postgresql')} {row['constraint_def']}"
        )

    statements: List[str] = []
    if include_drop:
        statements.append(f"DROP TABLE IF EXISTS {full_table} CASCADE;")
    statements.append(
        f"CREATE TABLE {full_table} (\n  " + ",\n  ".join(definitions) + "\n);"
    )

    for row in index_rows:
        index_def = str(row["index_def"]).rstrip(";")
        statements.append(f"{index_def};")
    return statements


def postgresql_data_statements(
    cursor: Any,
    schema: str,
    table_name: str,
    batch_size: int,
) -> List[str]:
    """Generate PostgreSQL INSERT statements for one table."""

    table_ident = f"{quote_identifier(schema, 'postgresql')}.{quote_identifier(table_name, 'postgresql')}"
    cursor.execute(f"SELECT * FROM {table_ident}")
    if not cursor.description:
        return []

    columns = [column[0] for column in cursor.description]
    statements: List[str] = []
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        statements.append(
            dump_insert_rows(
                table_name=table_name,
                columns=columns,
                rows=rows,
                engine="postgresql",
                schema=schema,
            )
        )
    return statements


def run_export(datasource: DataSource, args: argparse.Namespace) -> Dict[str, Any]:
    """Export schema/data SQL into one output file."""

    if args.schema_only and args.data_only:
        fail("--schema-only and --data-only cannot be used together")
    if args.batch_size <= 0:
        fail("--batch-size must be greater than 0")

    out_file = Path(args.out_file).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    requested_tables = parse_table_filter(
        args.tables or "",
        schema=(args.schema or "public"),
    )

    driver, connection = connect(datasource)
    del driver  # driver is selected in connect and not needed further.

    statements: List[str] = []
    exported_tables: List[str] = []

    try:
        with connection.cursor() as cursor:
            if datasource.engine in {"mysql", "mariadb"}:
                all_tables = mysql_table_names(cursor)
                selected_tables = filter_tables(all_tables, requested_tables)
                for table_name in selected_tables:
                    if not args.data_only:
                        statements.extend(
                            mysql_schema_statements(
                                cursor, table_name, args.include_drop
                            )
                        )
                    if not args.schema_only:
                        statements.extend(
                            mysql_data_statements(cursor, table_name, args.batch_size)
                        )
                    exported_tables.append(table_name)
            else:
                schema = args.schema or "public"
                all_tables = postgresql_table_names(cursor, schema)
                selected_tables = filter_tables(all_tables, requested_tables)
                for table_name in selected_tables:
                    if not args.data_only:
                        statements.extend(
                            postgresql_schema_statements(
                                cursor,
                                schema=schema,
                                table_name=table_name,
                                include_drop=args.include_drop,
                            )
                        )
                    if not args.schema_only:
                        statements.extend(
                            postgresql_data_statements(
                                cursor,
                                schema=schema,
                                table_name=table_name,
                                batch_size=args.batch_size,
                            )
                        )
                    exported_tables.append(f"{schema}.{table_name}")
    finally:
        connection.close()

    header = [
        "-- SQL dump generated by lystar-db-ops",
        f"-- Engine: {datasource.engine}",
        f"-- Database: {datasource.database}",
        f"-- Host: {datasource.host}:{datasource.port}",
        f"-- Exported at (UTC): {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "",
    ]
    content = "\n\n".join(header + statements).rstrip() + "\n"
    out_file.write_text(content, encoding="utf-8")

    return {
        "out_file": str(out_file),
        "bytes": out_file.stat().st_size,
        "table_count": len(exported_tables),
        "tables": exported_tables,
        "schema_only": bool(args.schema_only),
        "data_only": bool(args.data_only),
    }


def run_non_query(datasource: DataSource, sql: str) -> Dict[str, Any]:
    """Execute SQL script/DDL/DML and return execution stats."""

    driver, connection = connect(datasource)
    statement_count = 0
    affected_rows = 0

    try:
        if driver == "psycopg2":
            with connection.cursor() as cursor:
                cursor.execute(sql)
                statement_count = 1
                affected_rows = (
                    cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
                )
        else:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                if datasource.engine in {"mysql", "mariadb"}:
                    while True:
                        statement_count += 1
                        if (
                            cursor.description is None
                            and cursor.rowcount
                            and cursor.rowcount > 0
                        ):
                            affected_rows += int(cursor.rowcount)
                        if not cursor.nextset():
                            break
                else:
                    statement_count = 1
                    affected_rows = (
                        cursor.rowcount
                        if cursor.rowcount and cursor.rowcount > 0
                        else 0
                    )
        connection.commit()
        return {
            "statement_count": statement_count,
            "affected_rows": affected_rows,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_query(datasource: DataSource, sql: str, limit: int) -> Dict[str, Any]:
    """Execute SQL query and return row data."""

    driver, connection = connect(datasource)
    rows: List[Dict[str, Any]] = []
    columns: List[str] = []
    truncated = False

    try:
        if driver == "psycopg2":
            from psycopg2.extras import RealDictCursor  # type: ignore

            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(sql)
                if cursor.description:
                    columns = cursor_columns(cursor)
                    rows, truncated = fetch_query_rows(cursor, limit)
        else:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(sql)
                if cursor.description:
                    columns = cursor_columns(cursor)
                    rows, truncated = fetch_query_rows(cursor, limit)

        result = {
            "columns": columns,
            "returned_rows": len(rows),
            "limit": limit,
            "has_more": truncated,
            "truncated": truncated,
            "rows": rows,
        }
        if limit <= 0:
            result["row_count"] = len(rows)
        return result
    finally:
        connection.close()


def handle_discover(args: argparse.Namespace) -> None:
    """Handle datasource discovery command."""

    candidates, framework = collect_candidates(args)
    if not candidates:
        fail(
            "No datasource candidate found",
            {
                "project_root": str(Path(args.project_root).resolve()),
                "framework": framework,
            },
        )

    selected = pick_candidate(candidates, args.profile or "")
    selected = apply_overrides(selected, args)
    if CURRENT_OUTPUT == "full":
        payload: Dict[str, Any] = {
            "ok": True,
            "action": "discover",
            "framework": framework,
            "candidate_count": len(candidates),
            "selected": selected.masked(),
        }
    elif CURRENT_OUTPUT == "model":
        payload = {
            "ok": True,
            "action": "discover",
            "candidate_count": len(candidates),
            "selected": model_datasource(selected),
        }
    else:
        payload = {
            "ok": True,
            "action": "discover",
            "candidate_count": len(candidates),
            "selected": compact_datasource(selected),
        }
    if args.all:
        if CURRENT_OUTPUT == "full":
            payload["candidates"] = [item.masked() for item in candidates]
        elif CURRENT_OUTPUT == "model":
            payload["candidates"] = [model_datasource(item) for item in candidates]
        else:
            payload["candidates"] = [compact_datasource(item) for item in candidates]
    print_json(payload)


def handle_import(args: argparse.Namespace) -> None:
    """Handle SQL file import command."""

    datasource, candidates, framework = resolve_datasource(args)
    sql = load_sql_text("", args.sql_file)
    enforce_sql_safety(sql, args.allow_dangerous, action="import")
    result = run_non_query(datasource, sql)

    print_json(
        build_output_payload(
            action="import",
            framework=framework,
            candidates=candidates,
            datasource=datasource,
            result=result,
            extra={"sql_file": str(Path(args.sql_file).resolve())}
            if CURRENT_OUTPUT == "full"
            else None,
        )
    )


def handle_exec(args: argparse.Namespace) -> None:
    """Handle SQL execution command."""

    datasource, candidates, framework = resolve_datasource(args)
    sql = load_sql_text(args.sql, args.sql_file)
    enforce_sql_safety(sql, args.allow_dangerous, action="exec")
    result = run_non_query(datasource, sql)

    print_json(
        build_output_payload("exec", framework, candidates, datasource, result)
    )


def handle_query(args: argparse.Namespace) -> None:
    """Handle SQL query command."""

    datasource, candidates, framework = resolve_datasource(args)
    sql = load_sql_text(args.sql, args.sql_file)
    enforce_query_read_only(sql, args.allow_dangerous)
    result = run_query(
        datasource=datasource,
        sql=sql,
        limit=args.limit,
    )
    if args.output == "compact":
        result = compact_query_result(result, args.max_cell_chars)
    elif args.output == "model":
        result = model_query_result(
            result,
            max_cell_chars=args.max_cell_chars,
            row_format=args.row_format,
            with_row_number=args.with_row_number,
        )

    print_json(
        build_output_payload("query", framework, candidates, datasource, result)
    )


def handle_export(args: argparse.Namespace) -> None:
    """Handle SQL export command."""

    datasource, candidates, framework = resolve_datasource(args)
    result = run_export(datasource, args)

    print_json(
        build_output_payload("export", framework, candidates, datasource, result)
    )


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser."""

    parser = argparse.ArgumentParser(
        description="Connect MySQL/MariaDB/PostgreSQL, discover datasource, and run SQL operations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_datasource_args(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--project-root",
            default=str(Path.cwd()),
            help="Project root for auto datasource discovery",
        )
        target.add_argument(
            "--framework",
            default="auto",
            choices=[
                "auto",
                "spring",
                "springboot",
                "node",
                "nodejs",
                "python",
                "manual",
            ],
            help="Datasource discovery strategy",
        )
        target.add_argument(
            "--config-file",
            default="",
            help="Specific application*.yml/properties file path",
        )
        target.add_argument(
            "--profile", default="", help="Preferred Spring profile name"
        )
        target.add_argument("--name", default="default", help="Manual datasource alias")

        target.add_argument(
            "--db-type",
            default="",
            choices=["mysql", "mariadb", "postgresql", "postgres"],
        )
        target.add_argument(
            "--url", default="", help="Connection URL, supports JDBC and standard URL"
        )
        target.add_argument("--host", default="")
        target.add_argument("--port", type=int)
        target.add_argument("--database", default="")
        target.add_argument("--user", default="")
        target.add_argument("--password", default="")
        target.add_argument(
            "--output",
            default="model",
            choices=["model", "compact", "full"],
            help="Output detail level",
        )

    discover_parser = subparsers.add_parser(
        "discover", help="Discover datasource candidates"
    )
    add_datasource_args(discover_parser)
    discover_parser.add_argument(
        "--all", action="store_true", help="Show all discovered candidates"
    )

    import_parser = subparsers.add_parser("import", help="Import SQL script file")
    add_datasource_args(import_parser)
    import_parser.add_argument(
        "--sql-file", required=True, help="Absolute or relative SQL script file path"
    )
    import_parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Bypass default safety guard for destructive SQL",
    )

    exec_parser = subparsers.add_parser("exec", help="Execute SQL command/script")
    add_datasource_args(exec_parser)
    exec_group = exec_parser.add_mutually_exclusive_group(required=True)
    exec_group.add_argument("--sql", help="Inline SQL text")
    exec_group.add_argument("--sql-file", help="SQL file path")
    exec_parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Bypass default safety guard for destructive SQL",
    )

    query_parser = subparsers.add_parser("query", help="Run SQL query and return rows")
    add_datasource_args(query_parser)
    query_group = query_parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--sql", help="Inline SQL query text")
    query_group.add_argument("--sql-file", help="SQL file path")
    query_parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Allow non-read-only SQL in query mode",
    )
    query_parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max rows to return (0 means no truncation)",
    )
    query_parser.add_argument(
        "--max-cell-chars",
        type=int,
        default=300,
        help="Max query output characters per cell (0 means no truncation)",
    )
    query_parser.add_argument(
        "--row-format",
        choices=["array", "object"],
        default="array",
        help="Model output row shape",
    )
    query_parser.add_argument(
        "--with-row-number",
        action="store_true",
        help="Add a row number column in model output",
    )

    export_parser = subparsers.add_parser(
        "export", help="Export database schema/data to SQL file"
    )
    add_datasource_args(export_parser)
    export_parser.add_argument(
        "--out-file",
        required=True,
        help="Output SQL dump file path",
    )
    export_parser.add_argument(
        "--tables",
        default="",
        help="Comma-separated table list (default: all tables)",
    )
    export_parser.add_argument(
        "--schema",
        default="public",
        help="PostgreSQL schema name (default: public)",
    )
    export_mode_group = export_parser.add_mutually_exclusive_group()
    export_mode_group.add_argument(
        "--schema-only",
        action="store_true",
        help="Export only table schema",
    )
    export_mode_group.add_argument(
        "--data-only",
        action="store_true",
        help="Export only table data",
    )
    export_parser.add_argument(
        "--include-drop",
        action="store_true",
        help="Include DROP TABLE before CREATE TABLE",
    )
    export_parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows per INSERT statement batch",
    )

    return parser


def main() -> None:
    """Program entry."""

    global CURRENT_OUTPUT

    parser = build_parser()
    args = parser.parse_args()
    CURRENT_OUTPUT = getattr(args, "output", "compact")

    try:
        if args.command == "discover":
            handle_discover(args)
        elif args.command == "import":
            handle_import(args)
        elif args.command == "exec":
            handle_exec(args)
        elif args.command == "query":
            handle_query(args)
        elif args.command == "export":
            handle_export(args)
        else:
            fail("Unsupported command", {"command": args.command})
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - runtime safeguard
        fail(str(exc))


if __name__ == "__main__":
    main()
