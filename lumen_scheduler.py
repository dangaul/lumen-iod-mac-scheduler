#!/usr/bin/env python3
"""Mac-friendly Lumen bandwidth scheduler.

Evaluates day/time rules from a JSON config and applies the matching
bandwidth profile. Supports:
1) Native Lumen IoD workflow (inventory -> quote -> order)
2) Generic templated HTTP request mode
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import fcntl
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo

DAY_MAP = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
BANDWIDTH_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>mbps|gbps)\b", re.IGNORECASE)

BEGIN_TAG = "# BEGIN LUMEN_BANDWIDTH_SCHEDULER"
END_TAG = "# END LUMEN_BANDWIDTH_SCHEDULER"
LOGGER = logging.getLogger("lumen_scheduler")
LOG_ENABLED = True
LOG_INCLUDE_SENSITIVE = False
SENSITIVE_REPLACEMENTS = [
    (
        re.compile(r'("?(?:client_secret|api_key|basic_secret|access_token|authorization)"?\s*:\s*")[^"]*(")', re.IGNORECASE),
        r"\1***\2",
    ),
    (
        re.compile(r"(Authorization\s*[:=]\s*(?:Basic|Bearer)\s+)[^\s,}]+", re.IGNORECASE),
        r"\1***",
    ),
]


@dataclass
class EvaluationResult:
    profile_name: str
    profile: dict[str, Any]
    rule_name: str
    now_local: dt.datetime


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def configure_logging_from_config(config_path: Path, cli_log_path: Path) -> Path:
    global LOG_ENABLED, LOG_INCLUDE_SENSITIVE
    LOG_ENABLED = True
    LOG_INCLUDE_SENSITIVE = False
    resolved_log = cli_log_path
    if config_path.exists():
        raw = load_json(config_path)
        cfg = raw.get("logging", {})
        LOG_ENABLED = bool(cfg.get("enabled", True))
        LOG_INCLUDE_SENSITIVE = bool(cfg.get("include_sensitive", False))
        raw_log = str(cfg.get("file", "")).strip()
        if raw_log:
            p = Path(raw_log)
            resolved_log = p if p.is_absolute() else (config_path.parent / p).resolve()
    LOGGER.handlers.clear()
    LOGGER.disabled = not LOG_ENABLED
    if LOG_ENABLED:
        setup_logging(resolved_log)
    return resolved_log


def redact_sensitive_text(message: str) -> str:
    out = str(message)
    for pattern, replacement in SENSITIVE_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return out


def emit(message: str, *, error: bool = False) -> None:
    safe_message = str(message)
    if not LOG_INCLUDE_SENSITIVE:
        safe_message = redact_sensitive_text(safe_message)
    if error:
        print(safe_message, file=sys.stderr)
        if LOG_ENABLED:
            LOGGER.error(safe_message)
    else:
        print(safe_message)
        if LOG_ENABLED:
            LOGGER.info(safe_message)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def parse_dotenv_line(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        return None
    key, value = raw.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return key, value


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_dotenv_line(line)
        if not parsed:
            continue
        key, value = parsed
        os.environ[key] = value


def load_dotenv_near_config(config_path: Path) -> None:
    load_dotenv(config_path.parent / ".env")
    load_dotenv(Path.cwd() / ".env")


def expand_env_in_string(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in os.environ:
            return os.environ[key]
        if default is not None:
            return default
        raise ValueError(f"Missing required environment variable: {key}")

    return ENV_PATTERN.sub(repl, text)


def expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, str):
        return expand_env_in_string(value)
    return value


def load_config(path: Path) -> dict[str, Any]:
    load_dotenv_near_config(path)
    return expand_env(load_json(path))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def normalize_time(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


def day_matches(days: list[str], weekday: int) -> bool:
    allowed = set()
    for item in days:
        key = str(item).strip().lower()
        if key not in DAY_MAP:
            raise ValueError(f"Unknown day value: {item}")
        allowed.add(DAY_MAP[key])
    return weekday in allowed


def in_time_range(now_time: dt.time, start: dt.time, end: dt.time) -> bool:
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end


def rule_matches(rule: dict[str, Any], now_local: dt.datetime) -> bool:
    if "days" in rule and rule["days"]:
        if not day_matches(rule["days"], now_local.weekday()):
            return False

    if "dates" in rule and rule["dates"]:
        today = now_local.date().isoformat()
        if today not in set(rule["dates"]):
            return False

    if "time_ranges" in rule and rule["time_ranges"]:
        current = now_local.time().replace(second=0, microsecond=0)
        matches_any = False
        for rng in rule["time_ranges"]:
            start = normalize_time(rng["start"])
            end = normalize_time(rng["end"])
            if in_time_range(current, start, end):
                matches_any = True
                break
        if not matches_any:
            return False

    return True


def evaluate(config: dict[str, Any], now: dt.datetime | None = None) -> EvaluationResult:
    tz_name = config.get("timezone") or "America/Los_Angeles"
    tz = ZoneInfo(tz_name)
    now_local = now.astimezone(tz) if now else dt.datetime.now(tz)
    return evaluate_with_state(config, now_local=now_local, state={})


def active_override_profile(
    profiles: dict[str, Any],
    state: dict[str, Any],
    now_local: dt.datetime,
) -> tuple[str, dt.datetime] | None:
    override = state.get("override")
    if not isinstance(override, dict):
        return None

    profile_name = str(override.get("profile", "")).strip()
    until_utc_raw = str(override.get("until_utc", "")).strip()
    if not profile_name or profile_name not in profiles or not until_utc_raw:
        return None

    until_utc = dt.datetime.fromisoformat(until_utc_raw)
    if until_utc.tzinfo is None:
        until_utc = until_utc.replace(tzinfo=dt.timezone.utc)

    if now_local.astimezone(dt.timezone.utc) >= until_utc:
        return None

    return profile_name, until_utc.astimezone(now_local.tzinfo or dt.timezone.utc)


def evaluate_with_state(
    config: dict[str, Any],
    now_local: dt.datetime,
    state: dict[str, Any],
) -> EvaluationResult:

    profiles = config.get("profiles", {})
    if not profiles:
        raise ValueError("Config must include non-empty 'profiles'.")

    active_override = active_override_profile(profiles, state, now_local)
    if active_override:
        profile_name, until_local = active_override
        return EvaluationResult(
            profile_name=profile_name,
            profile=profiles[profile_name],
            rule_name=f"override_until_{until_local.strftime('%Y-%m-%dT%H:%M:%S%z')}",
            now_local=now_local,
        )

    holiday_profile = config.get("holiday_profile")
    holidays = set(config.get("holidays", []))
    if holiday_profile and now_local.date().isoformat() in holidays:
        if holiday_profile not in profiles:
            raise ValueError(f"holiday_profile '{holiday_profile}' not found in profiles")
        return EvaluationResult(
            profile_name=holiday_profile,
            profile=profiles[holiday_profile],
            rule_name="holiday",
            now_local=now_local,
        )

    for idx, rule in enumerate(config.get("rules", []), start=1):
        if not rule_matches(rule, now_local):
            continue
        profile_name = rule.get("profile")
        if profile_name not in profiles:
            raise ValueError(f"Rule {idx} references missing profile '{profile_name}'")
        return EvaluationResult(
            profile_name=profile_name,
            profile=profiles[profile_name],
            rule_name=rule.get("name", f"rule_{idx}"),
            now_local=now_local,
        )

    default_profile = config.get("default_profile")
    if default_profile not in profiles:
        raise ValueError("default_profile is missing or not defined in profiles")
    return EvaluationResult(
        profile_name=default_profile,
        profile=profiles[default_profile],
        rule_name="default",
        now_local=now_local,
    )


def profile_for_now(config: dict[str, Any], state: dict[str, Any]) -> EvaluationResult:
    tz_name = config.get("timezone") or "America/Los_Angeles"
    tz = ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    return evaluate_with_state(config, now_local=now_local, state=state)


def runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    # Keep runtime defaults in one place so cron, CLI, and dashboard behavior stay aligned.
    cfg = config.get("runtime", {}) if isinstance(config.get("runtime"), dict) else {}
    return {
        "lock_file": str(cfg.get("lock_file", ".lumen-bandwidth-run.lock")),
        "pending_poll_seconds": int(cfg.get("pending_poll_seconds", 30)),
        "pending_timeout_minutes": int(cfg.get("pending_timeout_minutes", 15)),
        "peak_lead_minutes": int(cfg.get("peak_lead_minutes", 15)),
        "off_peak_lead_minutes": int(cfg.get("off_peak_lead_minutes", 0)),
        "block_new_changes_while_pending": bool(cfg.get("block_new_changes_while_pending", True)),
    }


def lead_minutes_for_profile(config: dict[str, Any], profile_name: str) -> int:
    cfg = runtime_config(config)
    key = str(profile_name or "").strip().lower()
    if key == "peak":
        return max(0, int(cfg.get("peak_lead_minutes", 0)))
    if key == "off_peak":
        return max(0, int(cfg.get("off_peak_lead_minutes", 0)))
    return 0


def profile_for_now_with_lead(config: dict[str, Any], state: dict[str, Any]) -> EvaluationResult:
    tz_name = config.get("timezone") or "America/Los_Angeles"
    tz = ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    current = evaluate_with_state(config, now_local=now_local, state=state)

    # Manual overrides should run exactly as requested; lead time only applies to base schedule transitions.
    if active_override_profile(config.get("profiles", {}), state, now_local):
        return current

    max_lead = max(
        0,
        lead_minutes_for_profile(config, "peak"),
        lead_minutes_for_profile(config, "off_peak"),
    )
    if max_lead <= 0:
        return current

    base_state = dict(state)
    base_state.pop("override", None)
    for minutes_ahead in range(1, max_lead + 1):
        # Look ahead minute-by-minute so cron can submit before the scheduled transition.
        # This compensates for Lumen accepting changes before they become active.
        future = evaluate_with_state(
            config,
            now_local=now_local + dt.timedelta(minutes=minutes_ahead),
            state=base_state,
        )
        if future.profile_name == current.profile_name:
            continue
        if lead_minutes_for_profile(config, future.profile_name) >= minutes_ahead:
            return EvaluationResult(
                profile_name=future.profile_name,
                profile=future.profile,
                rule_name=f"lead_{minutes_ahead}m_before_{future.rule_name}",
                now_local=now_local,
            )
    return current


def replace_placeholders(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: replace_placeholders(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_placeholders(v, values) for v in value]
    if isinstance(value, str):
        result = value
        for k, v in values.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result
    return value


def build_basic_auth_value(auth_cfg: dict[str, Any]) -> str:
    if auth_cfg.get("basic_secret"):
        return str(auth_cfg["basic_secret"])

    client_id = auth_cfg.get("client_id")
    client_secret = auth_cfg.get("client_secret") or auth_cfg.get("api_key")
    if not client_id or not client_secret:
        raise ValueError(
            "auth requires either basic_secret, or client_id + (client_secret/api_key)"
        )

    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def fetch_token(auth_cfg: dict[str, Any], timeout: int) -> str:
    token_url = auth_cfg.get("token_url")
    if not token_url:
        raise ValueError("auth.token_url is required")

    payload = {"grant_type": auth_cfg.get("grant_type", "client_credentials")}
    if auth_cfg.get("scope"):
        payload["scope"] = auth_cfg["scope"]

    body = parse.urlencode(payload).encode("utf-8")
    req = request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Authorization", f"Basic {build_basic_auth_value(auth_cfg)}")

    with request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        token_payload = json.loads(resp.read().decode("utf-8"))

    token = token_payload.get("access_token")
    if not token:
        raise RuntimeError("No access_token returned by auth endpoint")
    return str(token)


def json_request(
    method: str,
    url: str,
    timeout: int,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, str]:
    req_body: bytes | None = None
    req_headers = headers.copy() if headers else {}
    if body is not None:
        req_body = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = request.Request(url, data=req_body, method=method.upper())
    for k, v in req_headers.items():
        req.add_header(k, v)

    try:
        with request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            code = getattr(resp, "status", None) or resp.getcode()
            text = resp.read().decode("utf-8", errors="ignore")
            return int(code), text
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        return int(exc.code), text


def normalize_bandwidth_label(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    low = text.lower()
    if low == "1gbps":
        return "1 Gbps"
    match = BANDWIDTH_RE.search(text)
    if match:
        num = match.group("num")
        unit = match.group("unit").lower()
        suffix = "Gbps" if unit == "gbps" else "Mbps"
        if "." in num:
            num = str(float(num)).rstrip("0").rstrip(".")
        return f"{num} {suffix}"
    return text


def is_placeholder_value(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text.startswith("${") or text.upper().startswith("YOUR_")


def inventory_service_type(iod_cfg: dict[str, Any]) -> str:
    return str(iod_cfg.get("inventory_service_type") or iod_cfg.get("product_name") or "Internet On-Demand").strip()


def inventory_urls(base_url: str, iod_cfg: dict[str, Any]) -> list[str]:
    # Prefer exact serviceId lookup, but keep serviceType as a fallback because Lumen has
    # returned different inventory behavior during pending/in-progress changes.
    urls: list[str] = []
    service_id = str(iod_cfg.get("service_id", "")).strip()
    if not is_placeholder_value(service_id):
        urls.append(f"{base_url}/ProductInventory/v1/inventory?{parse.urlencode({'serviceId': service_id})}")
    service_type = inventory_service_type(iod_cfg)
    if service_type:
        urls.append(f"{base_url}/ProductInventory/v1/inventory?{parse.urlencode({'serviceType': service_type})}")
    return list(dict.fromkeys(urls))


def inventory_url(base_url: str, iod_cfg: dict[str, Any]) -> str:
    urls = inventory_urls(base_url, iod_cfg)
    if not urls:
        return f"{base_url}/ProductInventory/v1/inventory"
    return urls[0]


def fetch_inventory_doc(
    base_url: str,
    iod_cfg: dict[str, Any],
    timeout: int,
    headers: dict[str, str],
) -> tuple[int, str, dict[str, Any] | None, str]:
    # Try every supported inventory lookup form before surfacing an error. This avoids
    # treating a transient serviceId lookup failure as a hard dashboard/scheduler failure.
    last_code = 0
    last_text = ""
    last_url = ""
    for url in inventory_urls(base_url, iod_cfg):
        last_url = url
        try:
            last_code, last_text = json_request("GET", url, timeout=timeout, headers=headers)
        except Exception as exc:
            last_code = 0
            last_text = str(exc)
            continue
        if 200 <= last_code <= 299:
            return last_code, last_text, json.loads(last_text), url
    return last_code, last_text, None, last_url


def inventory_characteristics(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    product = inventory.get("product", {}) or {}
    return product.get("productCharacteristic") or inventory.get("productCharacteristic") or []


def inventory_status(inventory: dict[str, Any]) -> str:
    product = inventory.get("product", {}) or {}
    return str(product.get("status") or inventory.get("status") or "")


def select_inventory_item(inv_doc: dict[str, Any], service_id: str) -> dict[str, Any] | None:
    inventory_items = inv_doc.get("serviceInventory") or []
    if not inventory_items:
        return None
    if service_id:
        for item in inventory_items:
            if str(item.get("serviceId", "")).strip() == service_id:
                return item
        return None
    return inventory_items[0]


def get_live_inventory_bandwidth(config: dict[str, Any]) -> tuple[bool, str]:
    iod_cfg = config.get("lumen_iod", {})
    base_url = str(iod_cfg.get("base_url", "https://api.lumen.com")).rstrip("/")
    customer_number = str(iod_cfg.get("customer_number", "")).strip()
    service_id = str(iod_cfg.get("service_id", "")).strip()
    timeout = int(iod_cfg.get("timeout_seconds", 20))
    if is_placeholder_value(customer_number) or is_placeholder_value(service_id):
        return False, "missing customer_number/service_id"
    auth_cfg = copy.deepcopy(iod_cfg.get("auth", {}))
    if not auth_cfg:
        return False, "missing lumen_iod.auth"
    auth_cfg.setdefault("token_url", f"{base_url}/oauth/v2/token")
    try:
        token = fetch_token(auth_cfg, timeout=timeout)
    except Exception as exc:
        return False, f"auth failed: {exc}"
    code, text, payload, _inv_url = fetch_inventory_doc(
        base_url,
        iod_cfg,
        timeout=timeout,
        headers={
            "Authorization": f"Bearer {token}",
            "x-customer-number": customer_number,
            "Accept": "application/json",
        },
    )
    if code < 200 or code > 299:
        return False, f"inventory failed HTTP {code}"
    try:
        if payload is None:
            payload = json.loads(text)
        item = select_inventory_item(payload, service_id)
        if not item:
            return False, f"inventory did not include serviceId {service_id}"
        for c in inventory_characteristics(item):
            if str(c.get("name", "")).strip().lower() == "bandwidth":
                return True, normalize_bandwidth_label(str(c.get("value", "")))
        return False, "bandwidth not found"
    except Exception as exc:
        return False, f"inventory parse failed: {exc}"


def bandwidth_from_inventory_item(item: dict[str, Any]) -> str:
    for c in inventory_characteristics(item):
        if str(c.get("name", "")).strip().lower() == "bandwidth":
            return normalize_bandwidth_label(str(c.get("value", "")))
    return ""


def get_live_inventory_state(config: dict[str, Any]) -> dict[str, Any]:
    iod_cfg = config.get("lumen_iod", {})
    base_url = str(iod_cfg.get("base_url", "https://api.lumen.com")).rstrip("/")
    customer_number = str(iod_cfg.get("customer_number", "")).strip()
    service_id = str(iod_cfg.get("service_id", "")).strip()
    timeout = int(iod_cfg.get("timeout_seconds", 20))
    if is_placeholder_value(customer_number) or is_placeholder_value(service_id):
        return {"ok": False, "error": "missing customer_number/service_id"}
    auth_cfg = copy.deepcopy(iod_cfg.get("auth", {}))
    if not auth_cfg:
        return {"ok": False, "error": "missing lumen_iod.auth"}
    auth_cfg.setdefault("token_url", f"{base_url}/oauth/v2/token")
    try:
        token = fetch_token(auth_cfg, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"auth failed: {exc}"}

    code, text, payload, inv_url = fetch_inventory_doc(
        base_url,
        iod_cfg,
        timeout=timeout,
        headers={
            "Authorization": f"Bearer {token}",
            "x-customer-number": customer_number,
            "Accept": "application/json",
        },
    )
    if code < 200 or code > 299:
        detail = (text or "").strip().replace("\n", " ")
        return {"ok": False, "http_code": code, "error": f"inventory failed HTTP {code}", "detail": detail, "url": inv_url}
    try:
        if payload is None:
            payload = json.loads(text)
        item = select_inventory_item(payload, service_id)
        if not item:
            return {"ok": False, "http_code": code, "error": f"inventory did not include serviceId {service_id}", "url": inv_url}
        return {
            "ok": True,
            "http_code": code,
            "status": inventory_status(item),
            "bandwidth": bandwidth_from_inventory_item(item),
            "url": inv_url,
        }
    except Exception as exc:
        return {"ok": False, "http_code": code, "error": f"inventory parse failed: {exc}", "detail": (text or "")[:400], "url": inv_url}


def profile_bandwidth(profile: dict[str, Any]) -> str:
    return normalize_bandwidth_label(
        str(profile.get("bandwidth") or profile.get("bandwidth_mbps") or "").strip()
    )


def extract_quote_id(details: str) -> str:
    match = re.search(r"quoteId=([A-Za-z0-9_.-]+)", str(details))
    return match.group(1) if match else ""


def pending_timeout_at(config: dict[str, Any], requested_at_utc: dt.datetime) -> str:
    timeout_minutes = max(1, int(runtime_config(config).get("pending_timeout_minutes", 15)))
    return (requested_at_utc + dt.timedelta(minutes=timeout_minutes)).isoformat()


def record_pending_change(
    config: dict[str, Any],
    state: dict[str, Any],
    result: EvaluationResult,
    details: str,
    source: str,
) -> None:
    # Lumen order acceptance only means the request was queued. Store a local pending
    # marker and wait for inventory to confirm the target bandwidth before marking success.
    now_utc = dt.datetime.now(dt.timezone.utc)
    target_bandwidth = profile_bandwidth(result.profile)
    state["pending_change"] = {
        "target_profile": result.profile_name,
        "target_bandwidth": target_bandwidth,
        "target_rule": result.rule_name,
        "source": source,
        "quote_id": extract_quote_id(details),
        "requested_at_utc": now_utc.isoformat(),
        "timeout_at_utc": pending_timeout_at(config, now_utc),
        "last_checked_at_utc": "",
        "last_live_status": "",
        "last_live_bandwidth": "",
        "timed_out": False,
    }
    state.update(
        {
            "last_run_at": now_utc.isoformat(),
            "last_run_result": "pending",
            "last_error": "",
            "profile_values": result.profile,
        }
    )


def parse_utc(value: str) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def refresh_pending_change(config: dict[str, Any], state: dict[str, Any], state_path: Path | None = None) -> str:
    # One inventory poll updates both user-visible status and the local state machine.
    # Callers should not submit another bandwidth change while this returns "pending".
    pending = state.get("pending_change")
    if not isinstance(pending, dict):
        return "none"

    now_utc = dt.datetime.now(dt.timezone.utc)
    live = get_live_inventory_state(config)
    pending["last_checked_at_utc"] = now_utc.isoformat()

    target_bandwidth = normalize_bandwidth_label(str(pending.get("target_bandwidth", "")))
    target_profile = str(pending.get("target_profile", "")).strip()
    live_status = ""
    live_bandwidth = ""

    if live.get("ok"):
        live_status = str(live.get("status", "")).strip()
        live_bandwidth = normalize_bandwidth_label(str(live.get("bandwidth", "")))
        pending["last_live_status"] = live_status
        pending["last_live_bandwidth"] = live_bandwidth
        pending.pop("last_error", None)
    else:
        pending["last_error"] = str(live.get("error", "live inventory check failed"))
        if live.get("detail"):
            pending["last_error_detail"] = str(live.get("detail"))

    timeout_at = parse_utc(str(pending.get("timeout_at_utc", "")))
    timed_out = bool(timeout_at and now_utc >= timeout_at)
    status_is_pending = live_status.lower() == "change pending"
    bandwidth_matches = bool(target_bandwidth and live_bandwidth and live_bandwidth.lower() == target_bandwidth.lower())

    if live.get("ok") and bandwidth_matches and not status_is_pending:
        completed = dict(pending)
        completed["completed_at_utc"] = now_utc.isoformat()
        state["last_completed_change"] = completed
        state.pop("pending_change", None)
        state.update(
            {
                "last_profile": target_profile,
                "last_rule": str(pending.get("target_rule", "")),
                "last_applied_at": now_utc.isoformat(),
                "last_run_at": now_utc.isoformat(),
                "last_run_result": "success",
                "last_error": "",
            }
        )
        if state_path is not None:
            save_json(state_path, state)
        return "resolved"

    if timed_out:
        pending["timed_out"] = True
        state.update(
            {
                "last_run_at": now_utc.isoformat(),
                "last_run_result": "error",
                "last_error": (
                    f"Pending change to {target_bandwidth or target_profile} timed out. "
                    f"Last live status={live_status or 'unknown'}, bandwidth={live_bandwidth or 'unknown'}."
                ),
            }
        )
        result = "timed_out"
    else:
        pending["timed_out"] = False
        state.update(
            {
                "last_run_at": now_utc.isoformat(),
                "last_run_result": "pending",
                "last_error": "",
            }
        )
        result = "pending"

    if state_path is not None:
        save_json(state_path, state)
    return result


def apply_lumen_iod_profile(
    config: dict[str, Any],
    evaluation: EvaluationResult,
    dry_run: bool,
) -> tuple[bool, str]:
    iod_cfg = config.get("lumen_iod", {})
    base_url = str(iod_cfg.get("base_url", "https://api.lumen.com")).rstrip("/")
    customer_number = str(iod_cfg.get("customer_number", "")).strip()
    service_id = str(iod_cfg.get("service_id", "")).strip()
    timeout = int(iod_cfg.get("timeout_seconds", 20))

    if is_placeholder_value(customer_number) or is_placeholder_value(service_id):
        raise ValueError("lumen_iod.customer_number and lumen_iod.service_id are required")

    bandwidth_value = str(
        evaluation.profile.get("bandwidth")
        or evaluation.profile.get("bandwidth_mbps")
        or ""
    ).strip()
    if not bandwidth_value:
        raise ValueError("Selected profile must include 'bandwidth' (or bandwidth_mbps)")

    auth_cfg = iod_cfg.get("auth", {})
    if not auth_cfg:
        raise ValueError("lumen_iod.auth is required")

    steps: list[dict[str, Any]] = []

    if dry_run:
        steps.append(
            {
                "step": "auth",
                "method": "POST",
                "url": auth_cfg.get("token_url") or f"{base_url}/oauth/v2/token",
                "headers": {"Authorization": "Basic ***"},
                "body": {"grant_type": auth_cfg.get("grant_type", "client_credentials")},
            }
        )
        token = "<oauth_token>"
    else:
        auth_cfg = copy.deepcopy(auth_cfg)
        auth_cfg.setdefault("token_url", f"{base_url}/oauth/v2/token")
        token = fetch_token(auth_cfg, timeout=timeout)

    common_headers = {
        "Authorization": f"Bearer {token}",
        "x-customer-number": customer_number,
        "Accept": "application/json",
    }

    inv_url = inventory_url(base_url, iod_cfg)
    if dry_run:
        steps.append(
            {
                "step": "inventory",
                "method": "GET",
                "url": inv_url,
                "headers": {
                    "Authorization": "Bearer ***",
                    "x-customer-number": customer_number,
                },
            }
        )
        inventory = {
            "serviceId": service_id,
            "status": "Active",
            "billingAccount": {
                "id": iod_cfg.get("billing_account_id", "ACCOUNT_ID"),
                "name": iod_cfg.get("billing_account_name", "ACCOUNT_NAME"),
            },
            "location": {"masterSiteid": iod_cfg.get("master_site_id", "MASTER_SITE_ID")},
            "locationProfile": {"dataCenter": bool(iod_cfg.get("use_partner_id", False))},
            "productCharacteristic": [],
            "product": {"status": "Active", "productCharacteristic": []},
        }
        if iod_cfg.get("use_partner_id"):
            inventory["locationProfile"]["relatedParty"] = {
                "id": iod_cfg.get("partner_id", "PARTNER_ID")
            }
            quote_identifier = str(iod_cfg.get("partner_id", "PARTNER_ID"))
            quote_identifier_key = "partnerId"
        else:
            quote_identifier = str(iod_cfg.get("port_service_id") or service_id)
            quote_identifier_key = "serviceId"
    else:
        inv_code, inv_text, inv_doc, _inv_url = fetch_inventory_doc(
            base_url,
            iod_cfg,
            timeout=timeout,
            headers=common_headers,
        )
        if inv_code < 200 or inv_code > 299:
            return False, f"Inventory lookup failed HTTP {inv_code}: {inv_text}"

        if inv_doc is None:
            inv_doc = json.loads(inv_text)
        inventory = select_inventory_item(inv_doc, service_id)
        if not inventory:
            return False, f"Inventory lookup did not include serviceId {service_id}"

        data_center = bool(inventory.get("locationProfile", {}).get("dataCenter", False))
        if data_center:
            quote_identifier = str(
                inventory.get("locationProfile", {})
                .get("relatedParty", {})
                .get("id", "")
            )
            quote_identifier_key = "partnerId"
        else:
            quote_identifier = str(iod_cfg.get("port_service_id") or "")
            for item in inventory_characteristics(inventory):
                if str(item.get("name", "")).strip().lower() == "uni service id":
                    quote_identifier = str(item.get("value", "")).strip()
                    break
            quote_identifier_key = "serviceId"

    if not quote_identifier:
        return False, "Unable to determine quote identifier (partnerId/serviceId) from config or inventory"

    master_site_id = str(
        iod_cfg.get("master_site_id")
        or inventory.get("location", {}).get("masterSiteid", "")
    ).strip()
    if dry_run and not master_site_id:
        master_site_id = "MASTER_SITE_ID"
    if not master_site_id:
        return False, "masterSiteId not found; set lumen_iod.master_site_id"

    billing_account_id = str(
        iod_cfg.get("billing_account_id")
        or inventory.get("billingAccount", {}).get("id", "")
    ).strip()
    billing_account_name = str(
        iod_cfg.get("billing_account_name")
        or inventory.get("billingAccount", {}).get("name", "")
    ).strip()
    if dry_run and not billing_account_id:
        billing_account_id = "ACCOUNT_ID"
    if dry_run and not billing_account_name:
        billing_account_name = "ACCOUNT_NAME"

    quote_body = {
        "sourceSystem": str(iod_cfg.get("source_system", "NaaS ExternalApi")),
        "customerNumber": customer_number,
        "currencyCode": str(iod_cfg.get("currency_code", "USD")),
        "masterSiteId": master_site_id,
        "productCode": str(iod_cfg.get("product_code", "718")),
        "productName": str(iod_cfg.get("product_name", "Internet On-Demand")),
        "speed": bandwidth_value,
        quote_identifier_key: quote_identifier,
    }
    if iod_cfg.get("customer_price_request_description"):
        quote_body["customerPriceRequestDescription"] = str(
            iod_cfg["customer_price_request_description"]
        )
    if iod_cfg.get("customer_purchase_order_number"):
        quote_body["customerPurchaseOrderNumber"] = str(
            iod_cfg["customer_purchase_order_number"]
        )

    quote_url = f"{base_url}/Product/v1/priceRequest"
    if dry_run:
        steps.append(
            {
                "step": "quote",
                "method": "POST",
                "url": quote_url,
                "headers": {
                    "Authorization": "Bearer ***",
                    "x-customer-number": customer_number,
                    "Content-Type": "application/json",
                },
                "body": quote_body,
            }
        )
        quote_id = "DRY_RUN_QUOTE_ID"
    else:
        quote_code, quote_text = json_request(
            "POST", quote_url, timeout=timeout, headers=common_headers, body=quote_body
        )
        if quote_code < 200 or quote_code > 299:
            return False, f"Quote request failed HTTP {quote_code}: {quote_text}"
        quote_id = str(json.loads(quote_text).get("id", "")).strip()
        if not quote_id:
            return False, f"Quote request did not return id: {quote_text}"

    contact = iod_cfg.get("related_contact", {})
    contact_info = {
        "number": str(contact.get("number", "")),
        "emailAddress": str(contact.get("email", "")),
        "role": str(contact.get("role", "Order Contact")),
        "organization": str(contact.get("organization", "")),
        "name": str(contact.get("name", "Lumen Scheduler")),
    }
    number_extension = str(contact.get("number_extension", "")).strip()
    if number_extension:
        contact_info["numberExtension"] = number_extension

    external_id = f"{service_id}{evaluation.now_local.strftime('%y%m%d')}{random.randint(0, 9999):04d}"[:20]
    order_body = {
        "externalId": external_id,
        "billingAccount": {
            "id": billing_account_id,
            "name": billing_account_name,
        },
        "channel": [{"id": "99", "name": "NaaS ExternalApi"}],
        "note": [{"text": f"Automated schedule ({evaluation.profile_name})"}],
        "productOrderItem": [
            {
                "id": service_id,
                "quantity": 1,
                "action": "modify",
                "product": {
                    "id": service_id,
                    "productCharacteristic": [],
                    "productSpecification": {
                        "id": str(iod_cfg.get("product_spec_id", "5001")),
                        "name": str(iod_cfg.get("product_spec_name", "NaaS Internet")),
                    },
                },
                "productOffering": {
                    "id": str(iod_cfg.get("product_code", "718")),
                    "name": str(iod_cfg.get("product_offering_name", "NaaS Internet")),
                },
            }
        ],
        "quote": [{"id": quote_id, "name": "quoteId"}],
    }
    contact_has_value = any(
        str(contact_info.get(k, "")).strip() for k in ["number", "emailAddress", "organization", "name"]
    )
    if contact_has_value:
        order_body["relatedContactInformation"] = [contact_info]

    order_url = f"{base_url}/Customer/v3/Ordering/orderRequest"
    if dry_run:
        steps.append(
            {
                "step": "order",
                "method": "POST",
                "url": order_url,
                "headers": {
                    "Authorization": "Bearer ***",
                    "x-customer-number": customer_number,
                    "Content-Type": "application/json",
                },
                "body": order_body,
            }
        )
        return True, json.dumps({"workflow": "lumen_iod", "steps": steps}, indent=2)

    order_code, order_text = json_request(
        "POST", order_url, timeout=timeout, headers=common_headers, body=order_body
    )
    if order_code < 200 or order_code > 299:
        return False, f"Order update failed HTTP {order_code}: {order_text}"

    verify = bool(iod_cfg.get("verify_after_order", False))
    wait_minutes = int(iod_cfg.get("wait_for_update_minutes", 5))
    if verify:
        time.sleep(max(0, wait_minutes) * 60)
        verify_code, verify_text, verify_doc, _verify_url = fetch_inventory_doc(
            base_url,
            iod_cfg,
            timeout=timeout,
            headers=common_headers,
        )
        if verify_code < 200 or verify_code > 299:
            return False, f"Post-update verification failed HTTP {verify_code}: {verify_text}"

        if verify_doc is None:
            verify_doc = json.loads(verify_text)
        items = verify_doc.get("serviceInventory") or []
        if items:
            bandwidth_seen = ""
            item = select_inventory_item(verify_doc, service_id) or items[0]
            for ch in inventory_characteristics(item):
                if str(ch.get("name", "")).strip().lower() == "bandwidth":
                    bandwidth_seen = str(ch.get("value", "")).strip()
                    break
            if bandwidth_seen and bandwidth_seen.lower() != bandwidth_value.lower():
                return (
                    False,
                    f"Verification mismatch. Expected '{bandwidth_value}', observed '{bandwidth_seen}'",
                )

    return True, f"Lumen IoD order submitted successfully (quoteId={quote_id})"


def apply_generic_profile(
    config: dict[str, Any],
    evaluation: EvaluationResult,
    dry_run: bool,
) -> tuple[bool, str]:
    api_cfg = copy.deepcopy(config.get("api", {}))
    req_cfg = api_cfg.get("request", {})
    if not req_cfg.get("url"):
        raise ValueError("api.request.url is required")

    timeout = int(api_cfg.get("timeout_seconds", 20))

    placeholders = {
        "profile": evaluation.profile_name,
        "rule": evaluation.rule_name,
        "timestamp": evaluation.now_local.isoformat(),
    }
    placeholders.update(evaluation.profile)

    url = replace_placeholders(req_cfg["url"], placeholders)
    method = str(req_cfg.get("method", "PATCH")).upper()
    headers = replace_placeholders(req_cfg.get("headers", {}), placeholders)
    body_template = replace_placeholders(req_cfg.get("body_template", {}), placeholders)

    auth_cfg = api_cfg.get("auth")
    if dry_run and auth_cfg:
        headers["Authorization"] = "Bearer <oauth_token>"
    elif auth_cfg:
        token = fetch_token(auth_cfg, timeout=timeout)
        headers["Authorization"] = f"Bearer {token}"

    if dry_run:
        preview = {
            "method": method,
            "url": url,
            "headers": {k: ("***" if k.lower() == "authorization" else v) for k, v in headers.items()},
            "body": body_template,
        }
        return True, json.dumps(preview, indent=2)

    code, text = json_request(method, url, timeout=timeout, headers=headers, body=body_template)
    accepted = set(api_cfg.get("success_status", [200, 201, 202, 204]))
    if code not in accepted:
        return False, f"Unexpected status {code}: {text}"
    return True, f"HTTP {code}: {text}".strip()


def apply_profile(config: dict[str, Any], evaluation: EvaluationResult, dry_run: bool = False) -> tuple[bool, str]:
    if config.get("lumen_iod"):
        return apply_lumen_iod_profile(config, evaluation, dry_run=dry_run)
    return apply_generic_profile(config, evaluation, dry_run=dry_run)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def state_path_from_config(config: dict[str, Any], config_path: Path) -> Path:
    raw = config.get("state_file", ".lumen-bandwidth-state.json")
    p = Path(raw)
    if p.is_absolute():
        return p
    return (config_path.parent / p).resolve()


def lock_path_from_config(config: dict[str, Any], config_path: Path) -> Path:
    raw = str(runtime_config(config).get("lock_file", ".lumen-bandwidth-run.lock"))
    p = Path(raw)
    if p.is_absolute():
        return p
    return (config_path.parent / p).resolve()


@contextmanager
def acquire_run_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    locked = False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            fh.seek(0)
            fh.truncate(0)
            fh.write(f"pid={os.getpid()} time={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
            fh.flush()
        except BlockingIOError:
            locked = False
        yield locked
    finally:
        if locked:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        fh.close()


def cleanup_expired_override(config: dict[str, Any], state: dict[str, Any]) -> bool:
    profiles = config.get("profiles", {})
    tz = ZoneInfo(config.get("timezone") or "America/Los_Angeles")
    now_local = dt.datetime.now(tz)
    still_active = active_override_profile(profiles, state, now_local)
    if still_active is not None:
        return False

    if "override" in state:
        state.pop("override", None)
        return True
    return False


def find_next_base_profile_time(
    config: dict[str, Any],
    base_state: dict[str, Any],
    now_local: dt.datetime,
    target_profile: str,
    lookahead_days: int = 14,
) -> dt.datetime | None:
    """Find the first future minute when base schedule profile becomes target_profile."""
    cur = now_local + dt.timedelta(minutes=1)
    end = now_local + dt.timedelta(days=max(1, lookahead_days))
    while cur <= end:
        res = evaluate_with_state(config, now_local=cur, state=base_state)
        if res.profile_name == target_profile:
            return cur
        cur += dt.timedelta(minutes=1)
    return None


def apply_override_until(
    config_path: Path,
    profile_name: str,
    until_local: dt.datetime,
    dry_run: bool,
    clip_to_schedule: bool = True,
) -> int:
    config = load_config(config_path)
    profiles = config.get("profiles", {})
    alias_map = {
        "peak": "peak",
        "on_peak": "peak",
        "on-peak": "peak",
        "off_peak": "off_peak",
        "off-peak": "off_peak",
        "offpeak": "off_peak",
    }
    profile_name = alias_map.get(profile_name.strip().lower(), profile_name.strip())
    if profile_name not in profiles:
        raise ValueError(f"Unknown profile '{profile_name}'. Available: {', '.join(sorted(profiles.keys()))}")

    state_path = state_path_from_config(config, config_path)
    state = load_state(state_path)
    if (
        runtime_config(config).get("block_new_changes_while_pending", True)
        and isinstance(state.get("pending_change"), dict)
    ):
        pending_status = refresh_pending_change(config, state, state_path if not dry_run else None)
        if pending_status in {"pending", "timed_out"}:
            emit(
                "pending_change=true action=skip_new_override "
                f"status={pending_status} target_bandwidth={state.get('pending_change', {}).get('target_bandwidth', '')}",
                error=(pending_status == "timed_out"),
            )
            return 2
    cleanup_expired_override(config, state)

    tz = ZoneInfo(config.get("timezone") or "America/Los_Angeles")
    now_local = dt.datetime.now(tz)
    if until_local.tzinfo is None:
        until_local = until_local.replace(tzinfo=tz)
    else:
        until_local = until_local.astimezone(tz)
    if until_local <= now_local:
        raise ValueError("Override end time must be in the future.")

    base_state = dict(state)
    base_state.pop("override", None)
    base_result = evaluate_with_state(config, now_local=now_local, state=base_state)
    if profile_name == base_result.profile_name and clip_to_schedule:
        raise ValueError(
            f"Override to '{profile_name}' is not allowed because schedule is already "
            f"'{base_result.profile_name}'."
        )

    requested_until_local = until_local
    if clip_to_schedule:
        next_same_profile = find_next_base_profile_time(
            config=config,
            base_state=base_state,
            now_local=now_local,
            target_profile=profile_name,
        )
        if next_same_profile and next_same_profile < requested_until_local:
            until_local = next_same_profile

    effective_minutes = int(max(1, round((until_local - now_local).total_seconds() / 60.0)))
    override_block = {
        "profile": profile_name,
        "until_utc": until_local.astimezone(dt.timezone.utc).isoformat(),
        "set_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "requested_duration_minutes": int(max(1, round((requested_until_local - now_local).total_seconds() / 60.0))),
        "effective_duration_minutes": int(effective_minutes),
        "override_mode": "until_local",
        "clip_to_schedule": bool(clip_to_schedule),
    }

    result = EvaluationResult(
        profile_name=profile_name,
        profile=profiles[profile_name],
        rule_name=f"manual_override_until_{until_local.strftime('%Y%m%dT%H%M')}",
        now_local=now_local,
    )
    prev_profile = state.get("last_profile")
    should_apply = prev_profile != profile_name
    emit(
        f"time={now_local.isoformat()} action=override profile={profile_name} "
        f"until={until_local.isoformat()} effective_duration_minutes={effective_minutes} "
        f"clip_to_schedule={clip_to_schedule}"
    )

    if should_apply:
        ok, details = apply_profile(config, result, dry_run=dry_run)
        if not ok:
            emit(f"action=apply success=false details={details}", error=True)
            if not dry_run:
                state.update(
                    {
                        "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "last_run_result": "error",
                        "last_error": details,
                    }
                )
                save_json(state_path, state)
            return 2
        emit(f"action=apply success=true details={details}")
        if not dry_run:
            record_pending_change(config, state, result, details, source="override")
    else:
        emit("action=apply success=true details=already_on_requested_profile")

    if not dry_run:
        state["override"] = override_block
        if not should_apply:
            state.update(
                {
                    "last_profile": profile_name,
                    "last_rule": result.rule_name,
                    "last_applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "last_run_result": "success",
                    "last_error": "",
                    "profile_values": result.profile,
                }
            )
        save_json(state_path, state)
    return 0


def run_override(
    config_path: Path,
    profile_name: str,
    duration_minutes: int,
    dry_run: bool,
) -> int:
    if duration_minutes < 1:
        raise ValueError("duration must be at least 1 minute")

    config = load_config(config_path)
    tz = ZoneInfo(config.get("timezone") or "America/Los_Angeles")
    now_local = dt.datetime.now(tz)
    until_local = now_local + dt.timedelta(minutes=duration_minutes)
    return apply_override_until(
        config_path=config_path,
        profile_name=profile_name,
        until_local=until_local,
        dry_run=dry_run,
        clip_to_schedule=True,
    )


def clear_override(config_path: Path) -> int:
    config = load_config(config_path)
    state_path = state_path_from_config(config, config_path)
    state = load_state(state_path)
    if "override" not in state:
        emit("override_present=false")
        return 0

    state.pop("override", None)
    save_json(state_path, state)
    emit("override_present=false action=cleared")
    return 0


def run_once(config_path: Path, force: bool, dry_run: bool) -> int:
    config = load_config(config_path)
    lock_path = lock_path_from_config(config, config_path)
    with acquire_run_lock(lock_path) as have_lock:
        if not have_lock:
            emit(f"run_lock=busy action=skip lock_file={lock_path}")
            return 0

        return _run_once_inner(config_path=config_path, config=config, force=force, dry_run=dry_run)


def _run_once_inner(config_path: Path, config: dict[str, Any], force: bool, dry_run: bool) -> int:
    state_path = state_path_from_config(config, config_path)
    state = load_state(state_path)
    if isinstance(state.get("pending_change"), dict) and not dry_run:
        pending_status = refresh_pending_change(config, state, state_path)
        pending = state.get("pending_change", {})
        if pending_status == "resolved":
            emit("pending_change=false action=resolved")
            return 0
        if pending_status == "timed_out":
            emit(
                "pending_change=true action=skip status=timed_out "
                f"target_bandwidth={pending.get('target_bandwidth', '')}",
                error=True,
            )
            return 2
        emit(
            "pending_change=true action=skip status=pending "
            f"target_bandwidth={pending.get('target_bandwidth', '')} "
            f"last_live_status={pending.get('last_live_status', '')} "
            f"last_live_bandwidth={pending.get('last_live_bandwidth', '')}"
        )
        return 0

    if cleanup_expired_override(config, state) and not dry_run:
        save_json(state_path, state)

    tz = ZoneInfo(config.get("timezone") or "America/Los_Angeles")
    now_local = dt.datetime.now(tz)
    active = active_override_profile(config.get("profiles", {}), state, now_local)
    if active:
        override_profile, _ = active
        base_state = dict(state)
        base_state.pop("override", None)
        base_result = evaluate_with_state(config, now_local=now_local, state=base_state)
        if base_result.profile_name == override_profile:
            state.pop("override", None)
            if not dry_run:
                save_json(state_path, state)
            emit(
                "override_auto_cleared=true reason=schedule_matches_override "
                f"profile={override_profile}"
            )

    result = profile_for_now_with_lead(config, state)

    prev_profile = state.get("last_profile")
    should_apply = force or prev_profile != result.profile_name

    if not force and not dry_run and not prev_profile and config.get("lumen_iod"):
        # Unknown local state (fresh machine/state reset): read live once and avoid unnecessary write-order.
        ok_live, live_bw_or_err = get_live_inventory_bandwidth(config)
        if ok_live:
            live_bw = normalize_bandwidth_label(live_bw_or_err)
            target_bw = normalize_bandwidth_label(str(result.profile.get("bandwidth", "")))
            if live_bw and target_bw and live_bw.lower() == target_bw.lower():
                emit("state_unknown=true live_matches_target=true action=skip")
                state.update(
                    {
                        "last_profile": result.profile_name,
                        "last_rule": result.rule_name,
                        "last_applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "last_run_result": "skip",
                        "last_error": "",
                        "profile_values": result.profile,
                    }
                )
                save_json(state_path, state)
                return 0
            emit(
                f"state_unknown=true live_matches_target=false live_bandwidth={live_bw} target_bandwidth={target_bw}"
            )
        else:
            emit(f"state_unknown=true live_check_failed={live_bw_or_err}")

    emit(f"time={result.now_local.isoformat()} rule={result.rule_name} profile={result.profile_name}")

    if not should_apply:
        emit("no_change=true action=skip")
        if not dry_run:
            state.update(
                {
                    "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "last_run_result": "skip",
                    "last_error": "",
                }
            )
            save_json(state_path, state)
        return 0

    ok, details = apply_profile(config, result, dry_run=dry_run)
    if ok:
        emit(f"action=apply success=true details={details}")
        if not dry_run:
            record_pending_change(config, state, result, details, source="schedule")
            save_json(state_path, state)
        return 0

    emit(f"action=apply success=false details={details}", error=True)
    if not dry_run:
        state.update(
            {
                "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "last_run_result": "error",
                "last_error": details,
            }
        )
        save_json(state_path, state)
    return 2


def render_cron_line(script_path: Path, config_path: Path, interval_minutes: int, log_path: Path, python_bin: str) -> str:
    schedule = f"*/{interval_minutes} * * * *"
    command = (
        f"{python_bin} {shell_quote(str(script_path))} "
        f"--config {shell_quote(str(config_path))} run >> {shell_quote(str(log_path))} 2>&1"
    )
    return f"{schedule} {command}"


def shell_quote(text: str) -> str:
    if re.match(r"^[A-Za-z0-9_./:-]+$", text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def current_crontab() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def install_cron(script_path: Path, config_path: Path, interval_minutes: int, log_path: Path, python_bin: str) -> None:
    if interval_minutes < 1 or interval_minutes > 60:
        raise ValueError("interval_minutes must be between 1 and 60")

    existing = current_crontab()
    filtered_lines: list[str] = []
    in_block = False
    for line in existing.splitlines():
        if line.strip() == BEGIN_TAG:
            in_block = True
            continue
        if line.strip() == END_TAG:
            in_block = False
            continue
        if not in_block:
            filtered_lines.append(line)

    new_block = [
        BEGIN_TAG,
        render_cron_line(script_path, config_path, interval_minutes, log_path, python_bin),
        END_TAG,
    ]
    updated = "\n".join([ln for ln in filtered_lines if ln.strip()] + [""] + new_block) + "\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)


def remove_cron() -> None:
    existing = current_crontab()
    if not existing:
        return

    kept: list[str] = []
    in_block = False
    for line in existing.splitlines():
        if line.strip() == BEGIN_TAG:
            in_block = True
            continue
        if line.strip() == END_TAG:
            in_block = False
            continue
        if not in_block:
            kept.append(line)

    if not kept:
        subprocess.run(["crontab", "-r"], check=False)
        return

    updated = "\n".join(kept).rstrip() + "\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lumen bandwidth scheduler")
    parser.add_argument("--config", default="./config.json", help="Path to JSON config")
    parser.add_argument("--log-file", default="./lumen-scheduler.log", help="Path to log file")

    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Evaluate schedule and apply matching profile")
    run_p.add_argument("--force", action="store_true", help="Apply even if profile did not change")
    run_p.add_argument("--dry-run", action="store_true", help="Print request and do not call API")

    sub.add_parser("status", help="Show currently matching rule/profile")

    override_p = sub.add_parser("override", help="Temporarily force a profile for a duration")
    override_p.add_argument(
        "--profile",
        default="peak",
        help="Profile to force: peak or off_peak (also accepts off-peak). Default: peak",
    )
    override_p.add_argument("--hours", type=float, default=1.0, help="Override duration in hours (default: 1)")
    override_p.add_argument("--dry-run", action="store_true", help="Preview API calls without applying")

    sub.add_parser("clear-override", help="Remove any active manual override")

    install_p = sub.add_parser("install-cron", help="Install cron entry for periodic execution")
    install_p.add_argument("--interval-minutes", type=int, default=5)
    install_p.add_argument("--log-file", default="./lumen-scheduler.log")
    install_p.add_argument("--python-bin", default="/usr/bin/env python3")

    sub.add_parser("remove-cron", help="Remove cron entry created by this tool")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    log_path = Path(args.log_file).expanduser().resolve()
    resolved_log_path = configure_logging_from_config(config_path, log_path)
    if LOG_ENABLED:
        LOGGER.info("command=%s config=%s log=%s", args.command, str(config_path), str(resolved_log_path))

    try:
        if args.command in {"run", "status", "override", "clear-override"} and not config_path.exists():
            emit(
                f"Config not found: {config_path}. "
                "Create it with: cp config.example.json config.json",
                error=True,
            )
            return 2

        if args.command == "run":
            return run_once(config_path, force=args.force, dry_run=args.dry_run)

        if args.command == "status":
            config = load_config(config_path)
            state_path = state_path_from_config(config, config_path)
            state = load_state(state_path)
            if isinstance(state.get("pending_change"), dict):
                refresh_pending_change(config, state, state_path)
            if cleanup_expired_override(config, state):
                save_json(state_path, state)
            result = profile_for_now_with_lead(config, state)
            emit(f"time={result.now_local.isoformat()}")
            emit(f"rule={result.rule_name}")
            emit(f"profile={result.profile_name}")
            emit(json.dumps(result.profile, indent=2, sort_keys=True))
            override = state.get("override")
            if override:
                emit(f"override={json.dumps(override, sort_keys=True)}")
            pending = state.get("pending_change")
            if pending:
                emit(f"pending_change={json.dumps(pending, sort_keys=True)}")
            return 0

        if args.command == "override":
            duration_minutes = int(round(args.hours * 60))
            return run_override(
                config_path=config_path,
                profile_name=args.profile,
                duration_minutes=duration_minutes,
                dry_run=args.dry_run,
            )

        if args.command == "clear-override":
            return clear_override(config_path)

        script_path = Path(__file__).resolve()
        if args.command == "install-cron":
            install_cron(
                script_path=script_path,
                config_path=config_path,
                interval_minutes=args.interval_minutes,
                log_path=log_path,
                python_bin=args.python_bin,
            )
            emit("Installed cron entry for Lumen bandwidth scheduler")
            return 0

        if args.command == "remove-cron":
            remove_cron()
            emit("Removed cron entry for Lumen bandwidth scheduler")
            return 0
    except Exception as exc:
        LOGGER.exception("Unhandled error")
        emit(f"Unhandled error: {exc}", error=True)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
