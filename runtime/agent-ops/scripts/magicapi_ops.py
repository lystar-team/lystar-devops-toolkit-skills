#!/usr/bin/env python3
"""Magic-API WebIDE and runtime API operations for LYStar."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Iterable, Mapping

import paths
import result_store
from config_store import load_toml, lock_files, write_toml


TOOL_NAME = "magicx"
CONFIG_FILE_NAME = "magicapi.toml"
STATE_DIR_NAME = "magicapi"
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SENSITIVE_KEY_RE = re.compile(
    r"password|passwd|token|cookie|authorization|secret|credential|api[-_]?key",
    re.IGNORECASE,
)
DEFAULTS = {
    "webide_base_path": "/webide",
    "runtime_base_path": "",
    "login_path": "/login",
    "resource_path": "/resource",
    "resource_file_path": "/resource/file",
    "resource_save_path": "/resource/file/api/save",
    "verify_tls": True,
    "default_group": "",
}


class MagicApiError(RuntimeError):
    """Magic-API request or local profile error."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.exit_code = exit_code


class RawResponse:
    def __init__(self, status: int, headers: Any, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def payload(self) -> Any:
        if not self.body:
            return None
        text = self.body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def config_file() -> Path:
    return paths.config_home() / CONFIG_FILE_NAME


def state_root() -> Path:
    return paths.state_home() / STATE_DIR_NAME


def load_config() -> dict[str, Any]:
    document = load_toml(config_file())
    profiles = document.get("profiles", {})
    if not isinstance(profiles, dict):
        raise MagicApiError("magicapi.toml 的 profiles 必须是表")
    document["profiles"] = profiles
    document.setdefault("default_profile", "")
    return document


def save_config(document: dict[str, Any]) -> None:
    write_toml(config_file(), document)


def profile_path(alias: str) -> Path:
    return state_root() / f"{alias}.json"


def backup_root(alias: str) -> Path:
    return state_root() / "backups" / alias


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MagicApiError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise MagicApiError(f"JSON 文件格式错误：{path}: {exc}") from exc


def mask_value(value: Any) -> str:
    return "***" if value not in (None, "") else ""


def redact(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    metadata_key = normalized_key.startswith(("has_", "is_")) or normalized_key.endswith(("_count", "_status"))
    if key and SENSITIVE_KEY_RE.search(key) and not metadata_key:
        return "***"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [redact(item, key) for item in value]
    return value


def emit(payload: Any) -> None:
    print(json.dumps(redact(payload), ensure_ascii=False, indent=2, default=str))


def remember(
    action: str,
    target: str,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    success: bool,
) -> None:
    try:
        result_store.save_snapshot(
            TOOL_NAME,
            action,
            Path.cwd(),
            redact(dict(request)),
            redact(dict(result)),
            success,
            target,
        )
    except (OSError, RuntimeError, ValueError):
        # 结果快照不能阻断真实 Magic-API 操作。
        pass


def validate_alias(alias: str) -> str:
    value = alias.strip()
    if not ALIAS_RE.fullmatch(value):
        raise MagicApiError("Profile 别名只能包含字母、数字、点、下划线和短横线")
    return value


def normalize_path(value: str, default: str = "") -> str:
    text = str(value or default).strip()
    if not text:
        return ""
    return "/" + text.strip("/")


def join_url(base_url: str, *parts: str) -> str:
    url = base_url.rstrip("/")
    for part in parts:
        if part:
            url += "/" + str(part).strip("/")
    return url


def profile_defaults(profile: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(DEFAULTS)
    result.update({str(key): value for key, value in profile.items()})
    result["base_url"] = str(result.get("base_url") or "").strip().rstrip("/")
    result["webide_base_path"] = normalize_path(result.get("webide_base_path"))
    result["runtime_base_path"] = normalize_path(result.get("runtime_base_path"))
    result["login_path"] = normalize_path(result.get("login_path"), "/login")
    result["resource_path"] = normalize_path(result.get("resource_path"), "/resource")
    result["resource_file_path"] = normalize_path(
        result.get("resource_file_path"), "/resource/file"
    )
    result["resource_save_path"] = normalize_path(
        result.get("resource_save_path"), "/resource/file/api/save"
    )
    result["header_sets"] = result.get("header_sets") if isinstance(result.get("header_sets"), dict) else {}
    return result


def profile_public(alias: str, raw_profile: Mapping[str, Any]) -> dict[str, Any]:
    profile = profile_defaults(raw_profile)
    header_sets = profile.get("header_sets", {})
    public_headers: dict[str, Any] = {}
    if isinstance(header_sets, Mapping):
        for set_name, values in header_sets.items():
            if isinstance(values, Mapping):
                public_headers[str(set_name)] = {
                    str(key): mask_value(value) for key, value in values.items()
                }
    return {
        "alias": alias,
        "base_url": profile.get("base_url", ""),
        "webide_base_path": profile.get("webide_base_path", ""),
        "runtime_base_path": profile.get("runtime_base_path", ""),
        "login_path": profile.get("login_path", ""),
        "resource_path": profile.get("resource_path", ""),
        "resource_file_path": profile.get("resource_file_path", ""),
        "resource_save_path": profile.get("resource_save_path", ""),
        "username": profile.get("username", ""),
        "password": mask_value(profile.get("password", "")),
        "verify_tls": bool(profile.get("verify_tls", True)),
        "default_group": profile.get("default_group", ""),
        "header_sets": public_headers,
    }


def resolve_alias(document: Mapping[str, Any], alias: str | None) -> str:
    profiles = document.get("profiles", {})
    if not isinstance(profiles, Mapping):
        raise MagicApiError("没有可用的 Magic-API Profile")
    if alias:
        value = validate_alias(alias)
        if value not in profiles:
            raise MagicApiError(f"未找到 Magic-API Profile：{value}")
        return value
    default = str(document.get("default_profile") or "").strip()
    if default and default in profiles:
        return default
    names = sorted(str(name) for name in profiles)
    if len(names) == 1:
        return names[0]
    if not names:
        raise MagicApiError("没有 Magic-API Profile，请先执行 profile add")
    raise MagicApiError("存在多个 Profile，请明确指定别名或先执行 profile use")


def get_profile(document: Mapping[str, Any], alias: str | None) -> tuple[str, dict[str, Any]]:
    selected = resolve_alias(document, alias)
    profile = document["profiles"].get(selected)
    if not isinstance(profile, Mapping):
        raise MagicApiError(f"Profile 格式错误：{selected}")
    normalized = profile_defaults(profile)
    if not normalized["base_url"]:
        raise MagicApiError(f"Profile 缺少 base_url：{selected}")
    return selected, normalized


def load_auth_state(alias: str) -> dict[str, Any]:
    path = profile_path(alias)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MagicApiError(f"读取登录态失败：{path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def save_auth_state(alias: str, state: Mapping[str, Any]) -> None:
    write_private_json(profile_path(alias), state)


def clear_auth_state(alias: str) -> None:
    profile_path(alias).unlink(missing_ok=True)


def state_public(alias: str) -> dict[str, Any]:
    state = load_auth_state(alias)
    cookies = state.get("cookies", {})
    return {
        "alias": alias,
        "logged_in": bool(state.get("magic_token") or cookies),
        "logged_at": state.get("logged_at", ""),
        "cookie_count": len(cookies) if isinstance(cookies, Mapping) else 0,
        "has_magic_token": bool(state.get("magic_token")),
        "last_status": state.get("last_status", ""),
        "last_error": state.get("last_error", ""),
    }


def extract_cookie_headers(headers: Any) -> dict[str, str]:
    values: list[str] = []
    try:
        values = list(headers.get_all("Set-Cookie") or [])
    except AttributeError:
        value = headers.get("Set-Cookie") if headers else None
        if value:
            values = [value]
    cookies: dict[str, str] = {}
    for value in values:
        jar = SimpleCookie()
        jar.load(value)
        for name, morsel in jar.items():
            cookies[name] = morsel.value
    return cookies


def extract_token(headers: Any, payload: Any) -> str:
    for key in ("magic-token", "magicToken", "token", "accessToken", "access_token"):
        value = headers.get(key) if headers else None
        if value:
            return str(value)

    def walk(value: Any) -> str:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower().replace("_", "-") in {
                    "magic-token",
                    "magictoken",
                    "token",
                    "accesstoken",
                    "access-token",
                } and isinstance(item, (str, int)):
                    return str(item)
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(payload)


def response_code(payload: Any) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("code")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def response_message(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return str(payload.get("message") or payload.get("msg") or "")
    return ""


class MagicApiClient:
    def __init__(self, alias: str, profile: Mapping[str, Any], timeout: float = 120.0) -> None:
        self.alias = alias
        self.profile = profile_defaults(profile)
        self.timeout = timeout

    def _context(self) -> ssl.SSLContext | None:
        if self.profile.get("verify_tls", True):
            return None
        return ssl._create_unverified_context()

    def _open(self, request: urllib.request.Request) -> RawResponse:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self._context()) as response:
                return RawResponse(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return RawResponse(exc.code, exc.headers, exc.read())
        except urllib.error.URLError as exc:
            raise MagicApiError(
                f"Magic-API 连接失败：{self.alias}",
                details={"reason": str(exc.reason), "url": request.full_url},
            ) from exc
        except TimeoutError as exc:
            raise MagicApiError(
                f"Magic-API 请求超时：{self.alias}",
                details={"url": request.full_url, "timeout": self.timeout},
            ) from exc

    def raw_request(
        self,
        method: str,
        url: str,
        *,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
        authenticated: bool = True,
        form: bool = False,
    ) -> RawResponse:
        request_headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "lystar-magicapi-ops",
        }
        if headers:
            request_headers.update({str(key): str(value) for key, value in headers.items()})
        if authenticated:
            state = load_auth_state(self.alias)
            token = str(state.get("magic_token") or "")
            cookies = state.get("cookies", {})
            if token:
                request_headers.setdefault("magic-token", token)
            if isinstance(cookies, Mapping) and cookies:
                request_headers.setdefault(
                    "Cookie", "; ".join(f"{key}={value}" for key, value in cookies.items())
                )
        data: bytes | None = None
        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
                request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            else:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
        return self._open(request)

    def login(self) -> dict[str, Any]:
        username = str(self.profile.get("username") or "")
        password = str(self.profile.get("password") or "")
        if not username:
            raise MagicApiError(f"Profile 缺少 username：{self.alias}")
        if not password:
            raise MagicApiError(f"Profile 缺少 password：{self.alias}")
        url = join_url(
            str(self.profile["base_url"]),
            str(self.profile.get("webide_base_path") or ""),
            str(self.profile.get("login_path") or "/login"),
        )
        raw = self.raw_request(
            "POST",
            url,
            payload={"username": username, "password": password},
            authenticated=False,
            form=True,
        )
        payload = raw.payload()
        token = extract_token(raw.headers, payload)
        cookies = extract_cookie_headers(raw.headers)
        login_code = response_code(payload)
        login_data = payload.get("data") if isinstance(payload, Mapping) else None
        if raw.status >= 400 or (login_code is not None and login_code >= 400) or login_data is False:
            raise MagicApiError(
                f"Magic-API 登录失败，账号或密码未通过：{self.alias}",
                details={"http_status": raw.status, "magic_code": login_code, "response": payload},
            )
        if not token and not cookies:
            raise MagicApiError(
                f"Magic-API 登录响应缺少 magic-token 或 Cookie：{self.alias}",
                details={"http_status": raw.status, "response": payload},
            )
        state = load_auth_state(self.alias)
        merged_cookies = dict(state.get("cookies", {})) if isinstance(state.get("cookies"), Mapping) else {}
        merged_cookies.update(cookies)
        state.update(
            {
                "version": 1,
                "profile": self.alias,
                "magic_token": token or state.get("magic_token", ""),
                "cookies": merged_cookies,
                "logged_at": utc_now(),
                "last_status": raw.status,
                "last_error": "",
            }
        )
        save_auth_state(self.alias, state)
        return {
            "profile": self.alias,
            "http_status": raw.status,
            "logged_at": state["logged_at"],
            "has_magic_token": bool(state.get("magic_token")),
            "cookie_count": len(merged_cookies),
        }

    def request(
        self,
        method: str,
        url: str,
        *,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        raw = self.raw_request(method, url, payload=payload, headers=headers)
        parsed = raw.payload()
        code = response_code(parsed)
        auth_failure = raw.status in {401, 403} or code in {401, 403}
        if auth_failure and retry_auth:
            self.login()
            return self.request(
                method,
                url,
                payload=payload,
                headers=headers,
                retry_auth=False,
            )
        if raw.status >= 400 or (code is not None and code >= 400):
            raise MagicApiError(
                f"Magic-API 请求失败：{method.upper()} {url}",
                details={
                    "http_status": raw.status,
                    "magic_code": code,
                    "message": response_message(parsed),
                    "response": parsed,
                },
            )
        return {
            "http_status": raw.status,
            "magic_code": code,
            "message": response_message(parsed),
            "response": parsed,
        }


def header_set(profile: Mapping[str, Any], name: str | None) -> dict[str, str]:
    sets = profile.get("header_sets", {})
    if not name:
        return {}
    if not isinstance(sets, Mapping) or name not in sets:
        raise MagicApiError(f"未找到请求头集合：{name}")
    values = sets[name]
    if not isinstance(values, Mapping):
        raise MagicApiError(f"请求头集合格式错误：{name}")
    return {str(key): str(value) for key, value in values.items()}


def parse_header(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise MagicApiError(f"请求头格式应为 Name=Value：{value.split('=', 1)[0]}")
    name, item = value.split("=", 1)
    name = name.strip()
    if not name:
        raise MagicApiError("请求头名称不能为空")
    return name, item


def resource_url(profile: Mapping[str, Any], suffix: str) -> str:
    return join_url(
        str(profile["base_url"]),
        str(profile.get("webide_base_path") or ""),
        suffix,
    )


def runtime_url(profile: Mapping[str, Any], api_path: str) -> str:
    path = normalize_path(api_path)
    if not path:
        raise MagicApiError("接口路径不能为空")
    return join_url(
        str(profile["base_url"]),
        str(profile.get("runtime_base_path") or ""),
        path,
    )


def response_data(response: Mapping[str, Any]) -> Any:
    payload = response.get("response")
    if isinstance(payload, Mapping) and "data" in payload:
        return payload["data"]
    return payload


def resource_items(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if any(key in value for key in ("id", "path", "resourcePath", "resource_path")):
            yield value
        for item in value.values():
            yield from resource_items(item)
    elif isinstance(value, list):
        for item in value:
            yield from resource_items(item)


def resource_id_from_path(client: MagicApiClient, profile: Mapping[str, Any], target: str) -> str:
    response = client.request("POST", resource_url(profile, profile["resource_path"]), payload={})
    candidates = list(resource_items(response_data(response)))
    wanted = target.rstrip("/") or "/"
    for item in candidates:
        values = [item.get(key) for key in ("path", "resourcePath", "resource_path", "url")]
        for value in values:
            if str(value or "").rstrip("/") == wanted:
                found = item.get("id") or item.get("fileId") or item.get("resourceId")
                if found:
                    return str(found)
    raise MagicApiError(f"资源列表中没有找到路径：{target}")


def command_profile_list(_args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    profiles = document["profiles"]
    return {
        "default_profile": document.get("default_profile", ""),
        "profiles": [profile_public(str(alias), item) for alias, item in sorted(profiles.items()) if isinstance(item, Mapping)],
        "config_file": str(config_file()),
    }


def command_profile_show(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    return {"profile": profile_public(alias, profile), "state": state_public(alias)}


def command_profile_add(args: argparse.Namespace) -> dict[str, Any]:
    alias = validate_alias(args.alias)
    document = load_config()
    existing = document["profiles"].get(alias, {})
    if existing and not args.update:
        raise MagicApiError(f"Profile 已存在：{alias}，修改时使用 --update")
    profile = dict(existing) if isinstance(existing, Mapping) else {}
    profile["base_url"] = args.base_url.rstrip("/")
    profile["username"] = args.username
    if args.password is not None:
        profile["password"] = args.password
    elif args.password_stdin:
        profile["password"] = sys.stdin.read().rstrip("\r\n")
    elif not profile.get("password") and sys.stdin.isatty():
        profile["password"] = getpass.getpass("Magic-API password: ")
    for key in (
        "webide_base_path",
        "runtime_base_path",
        "login_path",
        "resource_path",
        "resource_file_path",
        "resource_save_path",
        "default_group",
    ):
        value = getattr(args, key)
        if value is not None:
            profile[key] = value
    if args.no_verify_tls:
        profile["verify_tls"] = False
    elif args.verify_tls:
        profile["verify_tls"] = True
    profile.setdefault("header_sets", {})
    document["profiles"][alias] = profile
    if args.default or not document.get("default_profile"):
        document["default_profile"] = alias
    with lock_files(config_file()):
        save_config(document)
    return {"profile": profile_public(alias, profile), "default_profile": document.get("default_profile", "")}


def command_profile_use(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, _profile = get_profile(document, args.alias)
    document["default_profile"] = alias
    with lock_files(config_file()):
        save_config(document)
    return {"default_profile": alias}


def command_profile_remove(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise MagicApiError("删除 Profile 需要 --confirm")
    document = load_config()
    alias, _profile = get_profile(document, args.alias)
    del document["profiles"][alias]
    if document.get("default_profile") == alias:
        document["default_profile"] = ""
    with lock_files(config_file()):
        save_config(document)
    clear_auth_state(alias)
    return {"removed": alias, "config_file": str(config_file())}


def command_profile_header_set(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    values: dict[str, str] = {}
    if args.file:
        loaded = read_json(Path(args.file))
        if not isinstance(loaded, Mapping):
            raise MagicApiError("请求头文件必须是 JSON 对象")
        values.update({str(key): str(value) for key, value in loaded.items()})
    for value in args.header or []:
        name, item = parse_header(value)
        values[name] = item
    if not values:
        raise MagicApiError("至少提供一个 --header 或 --file")
    sets = profile.setdefault("header_sets", {})
    if not isinstance(sets, dict):
        sets = {}
        profile["header_sets"] = sets
    sets[args.name] = values
    document["profiles"][alias] = profile
    with lock_files(config_file()):
        save_config(document)
    return {"profile": profile_public(alias, profile), "header_set": args.name}


def command_login(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    result = MagicApiClient(alias, profile, args.timeout).login()
    remember("login", alias, {"profile": alias}, result, True)
    return result


def command_logout(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, _profile = get_profile(document, args.alias)
    clear_auth_state(alias)
    result = {"profile": alias, "logged_in": False}
    remember("logout", alias, {"profile": alias}, result, True)
    return result


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, _profile = get_profile(document, args.alias)
    result = state_public(alias)
    remember("status", alias, {"profile": alias}, result, True)
    return result


def command_resource_list(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    client = MagicApiClient(alias, profile, args.timeout)
    body: Any = read_json(Path(args.body)) if args.body else {}
    response = client.request("POST", resource_url(profile, profile["resource_path"]), payload=body)
    result = {"profile": alias, "action": "resource.list", **response}
    remember("resource.list", alias, {"profile": alias, "body": body}, result, True)
    return result


def command_resource_get(args: argparse.Namespace) -> dict[str, Any]:
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    client = MagicApiClient(alias, profile, args.timeout)
    resource_id = args.id or resource_id_from_path(client, profile, args.path)
    url = join_url(str(profile["base_url"]), str(profile.get("webide_base_path") or ""), profile["resource_file_path"], resource_id)
    response = client.request("GET", url)
    result = {"profile": alias, "action": "resource.get", "resource_id": resource_id, **response}
    remember("resource.get", resource_id, {"profile": alias, "resource_id": resource_id}, result, True)
    return result


def command_resource_save(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise MagicApiError("保存 Magic-API 接口需要 --confirm")
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    client = MagicApiClient(alias, profile, args.timeout)
    resource_id = args.id
    payload: Any
    old_detail: Any = None
    if resource_id:
        get_url = join_url(str(profile["base_url"]), str(profile.get("webide_base_path") or ""), profile["resource_file_path"], resource_id)
        old_detail = response_data(client.request("GET", get_url))
    if args.file:
        payload = read_json(Path(args.file))
        if not isinstance(payload, Mapping):
            raise MagicApiError("资源文件必须是 JSON 对象")
        payload = dict(payload)
    elif old_detail is not None:
        if not isinstance(old_detail, Mapping):
            raise MagicApiError("远端资源详情不是 JSON 对象")
        payload = dict(old_detail)
    else:
        raise MagicApiError("保存时需要 --file 或 --id")
    if resource_id:
        payload.setdefault("id", resource_id)
    if args.script:
        payload["script"] = Path(args.script).read_text(encoding="utf-8")
    if old_detail is not None:
        backup_path = backup_root(alias) / f"{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}-{resource_id}.json"
        write_private_json(backup_path, redact(old_detail))
    else:
        backup_path = None
    url = join_url(str(profile["base_url"]), str(profile.get("webide_base_path") or ""), profile["resource_save_path"])
    if args.auto is not None:
        url += f"?auto={args.auto}"
    response = client.request("POST", url, payload=payload)
    result = {
        "profile": alias,
        "action": "resource.save",
        "resource_id": resource_id or payload.get("id", ""),
        "backup_file": str(backup_path) if backup_path else "",
        **response,
    }
    remember("resource.save", str(resource_id or payload.get("id", "")), {"profile": alias, "resource_id": resource_id or payload.get("id", "")}, result, True)
    return result


def command_run(args: argparse.Namespace) -> dict[str, Any]:
    method = args.method.upper()
    if method != "GET" and not args.confirm:
        raise MagicApiError(f"{method} 接口执行需要 --confirm")
    document = load_config()
    alias, profile = get_profile(document, args.alias)
    client = MagicApiClient(alias, profile, args.timeout)
    headers = header_set(profile, args.header_set)
    for value in args.header or []:
        name, item = parse_header(value)
        headers[name] = item
    body: Any = read_json(Path(args.body)) if args.body else None
    url = runtime_url(profile, args.path)
    response = client.request(method, url, payload=body, headers=headers)
    result = {
        "profile": alias,
        "action": "run",
        "method": method,
        "path": normalize_path(args.path),
        "headers": sorted(headers),
        **response,
    }
    remember("run", normalize_path(args.path), {"profile": alias, "method": method, "path": normalize_path(args.path), "headers": sorted(headers)}, result, True)
    return result


def command_last(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = result_store.load_snapshot(TOOL_NAME, Path.cwd())
    if args.clear:
        result_store.clear_snapshot(TOOL_NAME, Path.cwd())
        return {"cleared": True}
    if args.summary:
        return result_store.snapshot_view(snapshot, True)
    return result_store.snapshot_view(snapshot, False)


def add_alias_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("alias", nargs="?", help="Magic-API Profile 别名")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LYStar Magic-API WebIDE and runtime operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile = subparsers.add_parser("profile", help="管理 Magic-API Profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_list = profile_sub.add_parser("list", help="列出 Profile")
    profile_list.set_defaults(func=command_profile_list)
    profile_show = profile_sub.add_parser("show", help="查看 Profile 和登录态")
    profile_show.add_argument("alias", nargs="?")
    profile_show.set_defaults(func=command_profile_show)
    profile_add = profile_sub.add_parser("add", help="新增或修改 Profile")
    profile_add.add_argument("alias")
    profile_add.add_argument("--base-url", required=True)
    profile_add.add_argument("--username", required=True)
    profile_add.add_argument("--password")
    profile_add.add_argument("--password-stdin", action="store_true")
    profile_add.add_argument("--update", action="store_true")
    profile_add.add_argument("--default", action="store_true")
    for option, default in (
        ("webide-base-path", None),
        ("runtime-base-path", None),
        ("login-path", None),
        ("resource-path", None),
        ("resource-file-path", None),
        ("resource-save-path", None),
        ("default-group", None),
    ):
        profile_add.add_argument(f"--{option}", dest=option.replace("-", "_"), default=default)
    profile_add.add_argument("--verify-tls", action="store_true")
    profile_add.add_argument("--no-verify-tls", action="store_true")
    profile_add.set_defaults(func=command_profile_add)
    profile_use = profile_sub.add_parser("use", help="设置默认 Profile")
    profile_use.add_argument("alias")
    profile_use.set_defaults(func=command_profile_use)
    profile_remove = profile_sub.add_parser("remove", help="删除 Profile")
    profile_remove.add_argument("alias")
    profile_remove.add_argument("--confirm", action="store_true")
    profile_remove.set_defaults(func=command_profile_remove)
    profile_headers = profile_sub.add_parser("header-set", help="保存接口执行请求头集合")
    profile_headers.add_argument("alias")
    profile_headers.add_argument("name")
    profile_headers.add_argument("--header", action="append")
    profile_headers.add_argument("--file")
    profile_headers.set_defaults(func=command_profile_header_set)

    login = subparsers.add_parser("login", help="登录 Magic-API")
    add_alias_argument(login)
    login.add_argument("--timeout", type=float, default=120.0)
    login.set_defaults(func=command_login)
    logout = subparsers.add_parser("logout", help="清理登录态")
    add_alias_argument(logout)
    logout.set_defaults(func=command_logout)
    status = subparsers.add_parser("status", help="查看登录态")
    add_alias_argument(status)
    status.set_defaults(func=command_status)

    resource = subparsers.add_parser("resource", help="管理 WebIDE 资源")
    resource_sub = resource.add_subparsers(dest="resource_command", required=True)
    resource_list = resource_sub.add_parser("list", help="查询资源列表")
    add_alias_argument(resource_list)
    resource_list.add_argument("--body")
    resource_list.add_argument("--timeout", type=float, default=120.0)
    resource_list.set_defaults(func=command_resource_list)
    resource_get = resource_sub.add_parser("get", help="查询资源详情")
    add_alias_argument(resource_get)
    target = resource_get.add_mutually_exclusive_group(required=True)
    target.add_argument("--id")
    target.add_argument("--path")
    resource_get.add_argument("--timeout", type=float, default=120.0)
    resource_get.set_defaults(func=command_resource_get)
    resource_save = resource_sub.add_parser("save", help="保存资源")
    add_alias_argument(resource_save)
    resource_save.add_argument("--id")
    resource_save.add_argument("--file")
    resource_save.add_argument("--script")
    resource_save.add_argument("--auto", choices=("0", "1"), default="0")
    resource_save.add_argument("--confirm", action="store_true")
    resource_save.add_argument("--timeout", type=float, default=120.0)
    resource_save.set_defaults(func=command_resource_save)

    run = subparsers.add_parser("run", help="执行运行接口")
    add_alias_argument(run)
    run.add_argument("--path", required=True)
    run.add_argument("--method", choices=("GET", "POST", "PUT", "DELETE"), default="GET")
    run.add_argument("--body")
    run.add_argument("--header-set")
    run.add_argument("--header", action="append")
    run.add_argument("--confirm", action="store_true")
    run.add_argument("--timeout", type=float, default=120.0)
    run.set_defaults(func=command_run)

    last = subparsers.add_parser("last", help="查看本会话上次结果")
    last.add_argument("--summary", action="store_true")
    last.add_argument("--clear", action="store_true")
    last.set_defaults(func=command_last)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = args.func(args)
        emit({"ok": True, **(result if isinstance(result, dict) else {"data": result})})
        return 0
    except MagicApiError as exc:
        payload: dict[str, Any] = {"ok": False, "error": exc.message}
        payload.update(exc.details)
        emit(payload)
        return exc.exit_code
    except FileNotFoundError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except KeyboardInterrupt:
        emit({"ok": False, "error": "操作已取消"})
        return 130
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
