#!/usr/bin/env python3
"""
Elasticsearch Cluster Triage Agent — Interactive Installer
==========================================================
Deploys tools, skills, agent, and workflow into any Kibana space.
Requires Python 3.8+ with no external dependencies.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Paths ──────────────────────────────────────────────────────────────────────
INSTALL_DIR = Path(__file__).resolve().parent
ROOT = INSTALL_DIR.parent
BUNDLE_DIR = ROOT / "kibana-agent-builder" / "es-cluster-triage"
WORKFLOW_ALERT_TEMPLATE = ROOT / "workflows" / "es-cluster-triage.workflow.yaml"
WORKFLOW_SCHEDULED_TEMPLATE = ROOT / "workflows" / "es-cluster-triage-scheduled.workflow.yaml"
CREDS_FILE = INSTALL_DIR / ".credentials.local"
LOG_FILE = INSTALL_DIR / "install.log"
INSTALLED_FILE = INSTALL_DIR / ".installed.json"

CONNECTOR_NAME = "ES Cluster Triage Slack"
CONNECTOR_TYPE_ID = ".slack"
WORKFLOW_ID_SUFFIX = "es-cluster-triage-summary"
AGENT_ID = "es-cluster-triage-agent"

TOTAL_STEPS = 9

# ── ANSI colours ───────────────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{R}"


# ── Logging ────────────────────────────────────────────────────────────────────
_log_file: Any = None


def open_log() -> None:
    global _log_file
    _log_file = open(LOG_FILE, "a", encoding="utf-8")
    _log_file.write(f"\n{'='*60}\n")
    _log_file.write(f"Install run: {datetime.now().isoformat()}\n")
    _log_file.write(f"{'='*60}\n")


def log(msg: str) -> None:
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()


def close_log() -> None:
    if _log_file:
        _log_file.close()


# ── Console helpers ────────────────────────────────────────────────────────────
def print_banner() -> None:
    print()
    print(c(CYAN, "╔══════════════════════════════════════════════════════════════╗"))
    print(c(CYAN, "║") + c(BOLD + WHITE, "  Elasticsearch Cluster Triage Agent — Installer v1.0       ") + c(CYAN, "  ║"))
    print(c(CYAN, "║") + c(DIM,          "  Deploys AI-powered triage to any Kibana Monitoring cluster ") + c(CYAN, " ║"))
    print(c(CYAN, "╚══════════════════════════════════════════════════════════════╝"))
    print()


def step(n: int, title: str) -> None:
    print(f"\n{c(CYAN, f'[{n}/{TOTAL_STEPS}]')} {c(BOLD, title)}")
    print(c(DIM, "─" * 60))
    log(f"\n[STEP {n}/{TOTAL_STEPS}] {title}")


def ok(msg: str) -> None:
    print(f"  {c(GREEN, '✓')} {msg}")
    log(f"  OK: {msg}")


def warn(msg: str) -> None:
    print(f"  {c(YELLOW, '⚠')}  {msg}")
    log(f"  WARN: {msg}")


def err(msg: str) -> None:
    print(f"  {c(RED, '✗')} {msg}")
    log(f"  ERR: {msg}")


def info(msg: str) -> None:
    print(f"  {c(DIM, '·')} {msg}")
    log(f"  INFO: {msg}")


def progress_bar(label: str, done: int, total: int, width: int = 30) -> None:
    pct = done / total if total else 1
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    end = "\r" if done < total else "\n"
    print(f"  {c(CYAN, bar)} {c(BOLD, f'{done}/{total}')}  {label:<46}", end=end, flush=True)


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{c(DIM, default)}]" if default else ""
    try:
        val = input(f"\n  {c(BOLD, prompt)}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return val or default


def ask_secret(prompt: str) -> str:
    try:
        val = getpass.getpass(f"\n  {c(BOLD, prompt)}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return val.strip()


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        val = input(f"\n  {c(BOLD, prompt)} {c(DIM, hint)}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    if not val:
        return default
    return val in ("y", "yes")


def hr() -> None:
    print(c(DIM, "  " + "─" * 58))


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def kibana_request(
    base_url: str,
    auth_hdr: tuple[str, str],
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    url = f"{base_url}{path}"
    req = Request(
        url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "kbn-xsrf": "true",
            "x-elastic-internal-origin": "Kibana",
            auth_hdr[0]: auth_hdr[1],
        },
        method=method.upper(),
    )
    log(f"  {method.upper()} {url}")
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            data = json.loads(text) if text else {}
            log(f"  → {resp.status}")
            return data
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        log(f"  → HTTP {exc.code}: {body_text[:300]}")
        raise RuntimeError(f"HTTP {exc.code} for {method} {path}: {body_text[:300]}") from exc
    except URLError as exc:
        log(f"  → Connection error: {exc.reason}")
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def es_request(
    es_url: str,
    auth_hdr: tuple[str, str],
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    url = f"{es_url}{path}"
    req = Request(
        url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            auth_hdr[0]: auth_hdr[1],
        },
        method=method.upper(),
    )
    log(f"  ES {method.upper()} {url}")
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            data = json.loads(text) if text else {}
            log(f"  → {resp.status}")
            return data
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        log(f"  → HTTP {exc.code}: {body_text[:300]}")
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:300]}") from exc
    except URLError as exc:
        log(f"  → Connection error: {exc.reason}")
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def resolve_concrete_indices(es_url: str, hdr: tuple[str, str], pattern: str) -> list[str]:
    """Return concrete index names matching pattern (wildcards expanded, open indices only)."""
    try:
        result = es_request(
            es_url, hdr, "GET",
            f"/_resolve/index/{pattern}?expand_wildcards=open",
            timeout=15,
        )
        return [idx["name"] for idx in result.get("indices", [])]
    except RuntimeError:
        return []


def space_path(space_id: str, path: str) -> str:
    if space_id == "default":
        return path
    return f"/s/{space_id}{path}"


def delete_if_exists(base_url: str, hdr: tuple[str, str], path: str) -> bool:
    try:
        kibana_request(base_url, hdr, "DELETE", path)
        return True
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return False
        raise


def get_if_exists(base_url: str, hdr: tuple[str, str], path: str) -> Any:
    try:
        return kibana_request(base_url, hdr, "GET", path)
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


# ── [1] Prerequisite check ─────────────────────────────────────────────────────
def check_prerequisites() -> None:
    step(1, "Prerequisite Check")

    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 8):
        ok(f"Python {major}.{minor}")
    else:
        err(f"Python 3.8+ required — found {major}.{minor}")
        raise SystemExit(1)

    if not BUNDLE_DIR.exists():
        err(f"Bundle directory not found: {BUNDLE_DIR}")
        err("Run this script from within the repo root.")
        raise SystemExit(1)

    manifest = load_json(BUNDLE_DIR / "manifest.json")
    tool_count = len(manifest.get("tool_files", []))
    skill_count = len(manifest.get("skill_files", []))
    info(f"Bundle: {tool_count} ES|QL tools, {skill_count} skill groups")
    ok("Bundle manifest valid")

    for tmpl in [WORKFLOW_ALERT_TEMPLATE, WORKFLOW_SCHEDULED_TEMPLATE]:
        if not tmpl.exists():
            err(f"Workflow template not found: {tmpl}")
            raise SystemExit(1)
    ok("Workflow templates found")


# ── API key guide ──────────────────────────────────────────────────────────────
def show_api_key_guide() -> None:
    print(f"""
  {c(BOLD + YELLOW, "API Key Setup")}
  {c(DIM, "─" * 56)}
  You need {c(BOLD, "one")} API key with both Kibana and Elasticsearch access.
  Paste this command in Kibana → Management → Dev Tools:

  {c(CYAN, "Single installer API key")} — deploys components and reads/aliases monitoring data

  {c(DIM, 'POST /_security/api_key')}
  {c(DIM, '{')}
  {c(DIM, '  "name": "es-cluster-triage-installer",')}
  {c(DIM, '  "role_descriptors": {')}
  {c(DIM, '    "triage-installer": {')}
  {c(DIM, '      "cluster": ["monitor"],')}
  {c(DIM, '      "indices": [{')}
  {c(DIM, '        "names": ["<your-monitoring-pattern>", "<your-log-pattern>"],')}
  {c(DIM, '        "privileges": ["manage", "read", "view_index_metadata"]')}
  {c(DIM, '      }],')}
  {c(DIM, '      "applications": [{')}
  {c(DIM, '        "application": "kibana-.kibana",')}
  {c(DIM, '        "privileges": ["all"],')}
  {c(DIM, '        "resources": ["*"]')}
  {c(DIM, '      }]')}
  {c(DIM, '    }')}
  {c(DIM, '  }')}
  {c(DIM, '}')}

  {c(YELLOW, "Notes:")}
  · Replace <your-monitoring-pattern> and <your-log-pattern> with the patterns
    you'll enter in step 5 (e.g. .monitoring-es-8-mb and filebeat-*)
  · Use {c(BOLD, 'manage')} (not just read) — the installer creates index aliases
  · Using username/password (elastic or superuser) also works — no key needed
    """)


# ── [2] Credential collection ──────────────────────────────────────────────────
def collect_credentials() -> dict[str, str]:
    step(2, "Connection Details")

    creds: dict[str, str] = {}

    print(f"\n  {c(CYAN, 'Kibana URL')}")
    kb_url = ask("Kibana URL (https://...)").rstrip("/")
    if not kb_url.startswith("http"):
        err("URL must start with http:// or https://")
        raise SystemExit(1)
    creds["KB_URL"] = kb_url

    print(f"\n  {c(CYAN, 'Elasticsearch URL')}")
    # Auto-suggest ES URL from KB URL pattern
    suggested_es = re.sub(r'\.kb\.', '.es.', kb_url) if '.kb.' in kb_url else ""
    es_url = ask("Elasticsearch URL (https://...)", suggested_es).rstrip("/")
    if not es_url.startswith("http"):
        err("URL must start with http:// or https://")
        raise SystemExit(1)
    creds["ES_URL"] = es_url

    print(f"\n  {c(CYAN, 'Authentication')}")
    print(f"  {c(DIM, '1')} API Key   {c(DIM, '2')} Username / Password")
    auth_choice = ask("Auth method", "1")

    if auth_choice == "2":
        username = ask("Username", "elastic")
        password = ask_secret("Password")
        creds["AUTH_TYPE"] = "basic"
        creds["ES_USERNAME"] = username
        creds["ES_PASSWORD"] = password
    else:
        api_key = ask_secret("API Key (id:secret or encoded)")
        creds["AUTH_TYPE"] = "apikey"
        creds["ES_API_KEY"] = api_key

    return creds


def build_auth_header(creds: dict[str, str]) -> tuple[str, str]:
    if creds.get("AUTH_TYPE") == "basic":
        token = base64.b64encode(
            f"{creds['ES_USERNAME']}:{creds['ES_PASSWORD']}".encode()
        ).decode()
        return ("Authorization", f"Basic {token}")
    api_key = creds.get("ES_API_KEY", "")
    if ":" in api_key:
        encoded = base64.b64encode(api_key.encode()).decode()
        return ("Authorization", f"ApiKey {encoded}")
    return ("Authorization", f"ApiKey {api_key}")


# ── [3] Auth validation ────────────────────────────────────────────────────────
def validate_auth(creds: dict[str, str], hdr: tuple[str, str]) -> None:
    step(3, "Validating Authentication")

    kb_url = creds["KB_URL"]
    es_url = creds["ES_URL"]

    info("Checking Kibana connectivity…")
    try:
        status = kibana_request(kb_url, hdr, "GET", "/api/status", timeout=15)
        lvl = status.get("status", {}).get("overall", {}).get("level", "unknown")
        kb_ver = status.get("version", {}).get("number", "?")
        ok(f"Kibana {kb_ver} reachable — status: {lvl}")
    except RuntimeError as exc:
        err(f"Kibana connection failed: {exc}")
        raise SystemExit(1)

    info("Checking Elasticsearch connectivity…")
    try:
        health = es_request(es_url, hdr, "GET", "/_cluster/health", timeout=15)
        cluster_name = health.get("cluster_name", "unknown")
        cluster_status = health.get("status", "unknown")
        ok(f"Elasticsearch reachable — cluster: {cluster_name} ({cluster_status})")
    except RuntimeError as exc:
        err(f"Elasticsearch connection failed: {exc}")
        raise SystemExit(1)


# ── [4] Namespace / space ──────────────────────────────────────────────────────
def collect_namespace(creds: dict[str, str], hdr: tuple[str, str]) -> str:
    step(4, "Kibana Space")

    kb_url = creds["KB_URL"]

    try:
        spaces = kibana_request(kb_url, hdr, "GET", "/api/spaces/space")
        space_ids = [s.get("id") for s in spaces if s.get("id")]
        if space_ids:
            info(f"Available spaces: {', '.join(space_ids)}")
    except RuntimeError:
        warn("Could not list spaces — will use the specified space")

    namespace = ask(
        "Kibana space ID to deploy into",
        "default",
    ).strip()

    if not re.match(r'^[a-z0-9][a-z0-9\-]*$', namespace):
        err("Space ID must be lowercase alphanumeric with hyphens (e.g. 'default', 'my-space').")
        raise SystemExit(1)

    if namespace == "default":
        ok("Using the Default space")
        return namespace

    existing = get_if_exists(kb_url, hdr, f"/api/spaces/space/{namespace}")
    if existing:
        ok(f"Space '{namespace}' exists")
    else:
        warn(f"Space '{namespace}' does not exist")
        if confirm(f"Create space '{namespace}'?", True):
            try:
                kibana_request(kb_url, hdr, "POST", "/api/spaces/space", body={
                    "id": namespace,
                    "name": namespace.replace("-", " ").title(),
                    "description": "Elasticsearch Cluster Triage Agent",
                    "color": "#00BFB3",
                })
                ok(f"Space '{namespace}' created")
            except RuntimeError as exc:
                err(f"Failed to create space: {exc}")
                raise SystemExit(1)
        else:
            err("Cannot proceed without a valid Kibana space.")
            raise SystemExit(1)

    return namespace


# ── [5] Datastream configuration ───────────────────────────────────────────────
def collect_and_validate_datastreams(
    creds: dict[str, str], hdr: tuple[str, str]
) -> dict[str, str]:
    step(5, "Datastream Configuration")

    es_url = creds["ES_URL"]

    print(f"""
  {c(CYAN, "Monitoring datastream")}
  The triage tools query Elasticsearch monitoring metrics.

  Common patterns:
    {c(DIM, '.monitoring-es-*')}          Stack Monitoring (all versions) — {c(GREEN, 'recommended default')}
    {c(DIM, '.monitoring-es-8-mb')}       Stack Monitoring v8 (internal)
    {c(DIM, 'metrics-elasticsearch.*')}   Elastic Agent metricbeat integration

  Leave blank to use the default: {c(BOLD, '.monitoring-es-*')}""")

    monitoring_ds = ask("Monitoring datastream/index pattern", ".monitoring-es-*").strip()
    if not monitoring_ds:
        monitoring_ds = ".monitoring-es-*"

    print(f"""
  {c(CYAN, "Log datastream")}
  Error log and security audit tools query log data.

  Common patterns:
    {c(DIM, '.monitoring-es-*')}          Same stream as monitoring (contains logs too)
    {c(DIM, 'logs-elasticsearch.*')}      Elastic Agent filebeat integration
    {c(DIM, '.logs-endpoint.*')}          Endpoint security logs

  Leave blank to use the same pattern as monitoring.""")

    log_ds_input = ask("Log datastream/index pattern (blank = same as monitoring)").strip()
    log_ds = log_ds_input if log_ds_input else monitoring_ds

    ok(f"Monitoring: {monitoring_ds}")
    if log_ds != monitoring_ds:
        ok(f"Logs:       {log_ds}")

    # ── Validate monitoring datastream ────────────────────────────────────────
    info(f"Validating monitoring datastream…")
    monitoring_doc_count = 0
    try:
        result = es_request(es_url, hdr, "GET", f"/{monitoring_ds}/_count", timeout=20)
        monitoring_doc_count = result.get("count", 0)
        if monitoring_doc_count > 0:
            ok(f"Monitoring stream: {monitoring_doc_count:,} documents found")
        else:
            warn("Monitoring stream exists but has 0 docs — Stack Monitoring may not be enabled yet")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc) or "index_not_found" in str(exc).lower():
            warn(f"'{monitoring_ds}' not found — agent will deploy but tools need monitoring data to return results")
        else:
            warn(f"Could not verify: {exc}")

    # ── ES|QL smoke test ──────────────────────────────────────────────────────
    if monitoring_doc_count > 0:
        info("Running ES|QL smoke test…")
        try:
            esql = es_request(
                es_url, hdr, "POST", "/_query",
                body={"query": f"FROM {monitoring_ds} | LIMIT 1 | KEEP @timestamp"},
                timeout=20,
            )
            cols = [col.get("name") for col in esql.get("columns", [])]
            if "@timestamp" in cols:
                ok("ES|QL query successful — monitoring data is accessible")
            else:
                warn("ES|QL returned unexpected schema — verify your datastream pattern")
        except RuntimeError as exc:
            warn(f"ES|QL smoke test: {exc}")

    # ── Create monitoring alias (es-monitoring) ──────────────────────────────
    monitoring_alias = "es-monitoring"
    monitoring_alias_created = False

    info("Resolving monitoring indices…")
    monitoring_indices = resolve_concrete_indices(es_url, hdr, monitoring_ds)

    # Also look for the Kibana monitoring sibling (e.g. .monitoring-kibana-8-mb)
    kibana_sibling = re.sub(r'\.monitoring-es', '.monitoring-kibana', monitoring_ds)
    if kibana_sibling != monitoring_ds:
        kb_idx = resolve_concrete_indices(es_url, hdr, kibana_sibling)
        if kb_idx:
            info(f"Found Kibana monitoring sibling: {', '.join(kb_idx)}")
            # Deduplicate while preserving order
            seen: set[str] = set(monitoring_indices)
            for idx in kb_idx:
                if idx not in seen:
                    monitoring_indices.append(idx)
                    seen.add(idx)

    if monitoring_indices:
        info(f"Found {len(monitoring_indices)} index(es): {', '.join(monitoring_indices[:4])}")
        try:
            actions = [
                {"add": {"index": idx, "alias": monitoring_alias, "is_write_index": False}}
                for idx in monitoring_indices
            ]
            es_request(es_url, hdr, "POST", "/_aliases", body={"actions": actions}, timeout=20)
            preview = ", ".join(monitoring_indices[:3]) + ("…" if len(monitoring_indices) > 3 else "")
            ok(f"Alias '{monitoring_alias}' → {preview}")
            monitoring_alias_created = True
        except RuntimeError as exc:
            exc_str = str(exc)
            if "already" in exc_str.lower() or "HTTP 400" in exc_str:
                ok(f"Alias '{monitoring_alias}' already exists")
                monitoring_alias_created = True
            else:
                warn(f"Could not create alias '{monitoring_alias}': {exc}")
    else:
        warn(f"No indices found for '{monitoring_ds}' — alias '{monitoring_alias}' skipped (deploy will still proceed)")

    # ── Create log alias (elastic-cloud-logs-8) ───────────────────────────────
    log_alias = "elastic-cloud-logs-8"
    log_alias_created = False

    if log_ds == "elastic-cloud-logs-8":
        ok(f"Log pattern is '{log_alias}' — no alias needed")
        log_alias_created = True
    else:
        info("Resolving log indices…")
        log_indices = resolve_concrete_indices(es_url, hdr, log_ds)
        if log_indices:
            info(f"Found {len(log_indices)} log index(es): {', '.join(log_indices[:4])}")
            try:
                actions = [
                    {"add": {"index": idx, "alias": log_alias, "is_write_index": False}}
                    for idx in log_indices
                ]
                es_request(es_url, hdr, "POST", "/_aliases", body={"actions": actions}, timeout=20)
                preview = ", ".join(log_indices[:3]) + ("…" if len(log_indices) > 3 else "")
                ok(f"Alias '{log_alias}' → {preview}")
                log_alias_created = True
            except RuntimeError as exc:
                exc_str = str(exc)
                if "already" in exc_str.lower() or "HTTP 400" in exc_str:
                    ok(f"Alias '{log_alias}' already exists")
                    log_alias_created = True
                else:
                    warn(f"Could not create alias '{log_alias}': {exc}")
                    info(f"Log tools will query '{log_ds}' directly — ensure your API key covers it")
        else:
            warn(f"No indices found for '{log_ds}' — alias '{log_alias}' skipped")

    # ── API key coverage reminder ─────────────────────────────────────────────
    if not monitoring_alias_created or (not log_alias_created and log_ds != "elastic-cloud-logs-8"):
        print(f"""
  {c(YELLOW, "API key coverage check")}
  One or more aliases could not be created. Make sure your API key has
  {c(BOLD, "manage")} + {c(BOLD, "read")} + {c(BOLD, "view_index_metadata")} on these patterns:

    {c(BOLD, monitoring_ds)}  ← monitoring tools
    {c(BOLD, log_ds)}  ← error log and audit security tools
        """)

    return {
        "monitoring_ds": monitoring_ds,
        "log_ds": log_ds,
        "monitoring_alias": monitoring_alias,
        "monitoring_alias_created": monitoring_alias_created,
        "log_alias": log_alias,
        "log_alias_created": log_alias_created,
        "doc_count": monitoring_doc_count,
    }


# ── Credentials file ───────────────────────────────────────────────────────────
def save_credentials(creds: dict[str, str], namespace: str, ds_info: dict[str, str]) -> None:
    data = {
        "saved_at": datetime.now().isoformat(),
        "namespace": namespace,
        "kb_url": creds["KB_URL"],
        "es_url": creds["ES_URL"],
        "auth_type": creds.get("AUTH_TYPE", "apikey"),
        "monitoring_ds": ds_info["monitoring_ds"],
        "log_ds": ds_info["log_ds"],
        "monitoring_alias": ds_info["monitoring_alias"],
        "log_alias": ds_info["log_alias"],
    }
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    CREDS_FILE.chmod(0o600)
    log(f"Session metadata saved to {CREDS_FILE}")


# ── Deployment helpers ─────────────────────────────────────────────────────────
def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def provision_connector(kb_url: str, hdr: tuple[str, str], space_id: str, webhook_url: str) -> str:
    connectors = kibana_request(kb_url, hdr, "GET", space_path(space_id, "/api/actions/connectors"))
    if isinstance(connectors, list):
        for conn in connectors:
            if conn.get("name") == CONNECTOR_NAME:
                cid = conn.get("id")
                if cid:
                    log(f"  Reusing existing connector: {cid}")
                    return cid

    connector_id = str(uuid.uuid4())
    resp = kibana_request(
        kb_url, hdr, "POST",
        space_path(space_id, f"/api/actions/connector/{connector_id}"),
        body={"name": CONNECTOR_NAME, "connector_type_id": CONNECTOR_TYPE_ID,
              "config": {}, "secrets": {"webhookUrl": webhook_url}},
    )
    return resp.get("id", connector_id)


def deploy_workflow_yaml(
    kb_url: str, hdr: tuple[str, str], namespace: str,
    workflow_id: str, yaml_text: str, wf_name: str = "",
) -> None:
    """POST first, fall back to PUT on 409 (Kibana workflow IDs are globally unique)."""
    wf_path = space_path(namespace, f"/api/workflows/workflow/{workflow_id}")
    body: dict[str, Any] = {"id": workflow_id, "yaml": yaml_text}
    if wf_name:
        body["name"] = wf_name
    try:
        kibana_request(kb_url, hdr, "POST", space_path(namespace, "/api/workflows/workflow"), body=body)
    except RuntimeError as exc:
        if "HTTP 409" not in str(exc):
            raise
        put_body: dict[str, Any] = {"yaml": yaml_text}
        if wf_name:
            put_body["name"] = wf_name
        try:
            kibana_request(kb_url, hdr, "PUT", wf_path, body=put_body)
        except RuntimeError as put_exc:
            warn(f"Workflow update: {put_exc}")


# ── [6] Deploy ─────────────────────────────────────────────────────────────────
def deploy_all(
    creds: dict[str, str],
    hdr: tuple[str, str],
    namespace: str,
    ds_info: dict[str, str],
) -> dict[str, Any]:
    step(6, "Deploying Agent Components")

    kb_url = creds["KB_URL"]
    manifest = load_json(BUNDLE_DIR / "manifest.json")
    agent_data = load_json(BUNDLE_DIR / manifest["agent"])
    skills = [load_json(BUNDLE_DIR / p) for p in manifest.get("skill_files", [])]
    tools = [load_json(BUNDLE_DIR / p) for p in manifest.get("tool_files", [])]

    # Agent ID in manifest
    deployed_agent_id = agent_data.get("id", AGENT_ID)

    installed: dict[str, Any] = {
        "installed_at": datetime.now().isoformat(),
        "namespace": namespace,
        "kb_url": kb_url,
        "es_url": creds.get("ES_URL", ""),
        "tools": [],
        "skills": [],
        "agent_id": None,
        "workflows": [],
        "connector_id": None,
        "monitoring_alias": ds_info.get("monitoring_alias", "es-monitoring"),
        "log_alias": ds_info.get("log_alias", "elastic-cloud-logs-8"),
    }

    total_deploy = len(tools) + len(skills) + 1  # tools + skills + agent (workflows added later)
    done = 0

    def tick(label: str) -> None:
        nonlocal done
        done += 1
        progress_bar(label, done, total_deploy)

    # ── Patch tool queries to use aliases ─────────────────────────────────────
    monitoring_alias = ds_info.get("monitoring_alias", "es-monitoring")
    log_ds = ds_info["log_ds"]
    log_alias = ds_info.get("log_alias", "elastic-cloud-logs-8")
    log_alias_created = ds_info.get("log_alias_created", False)

    for tool in tools:
        cfg = tool.get("configuration", {})
        query = cfg.get("query", "")
        # Always rewrite monitoring pattern → es-monitoring alias
        query = re.sub(r'FROM \.monitoring-es-\S+', f"FROM {monitoring_alias}", query)
        # Log tools: keep elastic-cloud-logs-8 if alias was created; otherwise use raw pattern
        if not log_alias_created and log_ds != "elastic-cloud-logs-8":
            query = query.replace("FROM elastic-cloud-logs-8", f"FROM {log_ds}")
        cfg["query"] = query
        tool["configuration"] = cfg

    log_target = log_alias if log_alias_created else log_ds
    ok(f"Tool queries updated: monitoring → {monitoring_alias}, logs → {log_target}")

    # ── Tear down existing ─────────────────────────────────────────────────────
    info("Removing previous installation if any…")
    delete_if_exists(kb_url, hdr, space_path(namespace, f"/api/agent_builder/agents/{deployed_agent_id}"))
    for sk in skills:
        delete_if_exists(kb_url, hdr, space_path(namespace, f"/api/agent_builder/skills/{sk['id']}"))
    for t in tools:
        delete_if_exists(kb_url, hdr, space_path(namespace, f"/api/agent_builder/tools/{t['id']}"))

    # ── Tools ─────────────────────────────────────────────────────────────────
    for tool in tools:
        try:
            kibana_request(kb_url, hdr, "POST", space_path(namespace, "/api/agent_builder/tools"), body=tool)
            installed["tools"].append(tool["id"])
        except RuntimeError as exc:
            if "HTTP 409" in str(exc):
                installed["tools"].append(tool["id"])
            else:
                raise
        tick(f"Tool: {tool['id'][:44]}")

    # ── Skills ────────────────────────────────────────────────────────────────
    for skill in skills:
        try:
            kibana_request(kb_url, hdr, "POST", space_path(namespace, "/api/agent_builder/skills"), body=skill)
            installed["skills"].append(skill["id"])
        except RuntimeError as exc:
            if "HTTP 409" in str(exc):
                installed["skills"].append(skill["id"])
            else:
                raise
        tick(f"Skill: {skill['id'][:44]}")

    # ── Agent ─────────────────────────────────────────────────────────────────
    try:
        kibana_request(kb_url, hdr, "POST", space_path(namespace, "/api/agent_builder/agents"), body=agent_data)
        installed["agent_id"] = deployed_agent_id
    except RuntimeError as exc:
        if "HTTP 409" in str(exc):
            installed["agent_id"] = deployed_agent_id
        else:
            raise
    tick(f"Agent: {deployed_agent_id}")

    # Save install manifest now (workflows added in next step)
    INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
    INSTALLED_FILE.chmod(0o600)

    return installed


# ── [7] Workflow deployment ────────────────────────────────────────────────────
def deploy_workflows(
    creds: dict[str, str],
    hdr: tuple[str, str],
    namespace: str,
    ds_info: dict[str, str],
    installed: dict[str, Any],
) -> None:
    step(7, "Workflow Setup")

    kb_url = creds["KB_URL"]
    monitoring_ds = ds_info["monitoring_ds"]
    deployed_agent_id = installed.get("agent_id", AGENT_ID)

    print(f"""
  {c(CYAN, "Workflow Options")}
  Workflows trigger triage automatically and produce AI summaries.

  {c(DIM, '1')} {c(BOLD, 'Alert trigger')}    — runs when a Kibana alert fires (e.g. cluster health rule)
  {c(DIM, '2')} {c(BOLD, 'Scheduled')}        — runs on a fixed interval (hourly/daily health check)
  {c(DIM, '3')} {c(BOLD, 'Both')}             — deploy alert + scheduled variants
  {c(DIM, '4')} {c(BOLD, 'Skip')}             — deploy agent only, no workflow
    """)

    wf_choice = ask("Workflow type", "1").strip()
    deployed_wfs: list[str] = []

    def render_template(template_path: Path) -> str:
        yaml = template_path.read_text()
        yaml = yaml.replace("__METRICS_PATTERN__", monitoring_ds)
        yaml = yaml.replace("__AGENT_ID__", deployed_agent_id)
        return yaml

    if wf_choice in ("1", "3"):
        wf_id = f"{namespace}-{WORKFLOW_ID_SUFFIX}-alert" if namespace != "default" else f"{WORKFLOW_ID_SUFFIX}-alert"
        delete_if_exists(kb_url, hdr, space_path(namespace, f"/api/workflows/workflow/{wf_id}"))
        info(f"Deploying alert workflow: {wf_id}")
        deploy_workflow_yaml(kb_url, hdr, namespace, wf_id, render_template(WORKFLOW_ALERT_TEMPLATE),
                             wf_name="ES Cluster Triage Summary")
        ok(f"Alert workflow deployed: {wf_id}")
        deployed_wfs.append(wf_id)

    if wf_choice in ("2", "3"):
        print(f"""
  {c(CYAN, "Schedule interval")}
  How often the scheduled triage should run.
  Examples: {c(DIM, '30m')}  {c(DIM, '1h')}  {c(DIM, '4h')}  {c(DIM, '24h')}
        """)
        interval = ask("Interval", "1h").strip()
        # Normalize bare number → hours (e.g. "24" → "24h")
        if re.match(r'^\d+$', interval):
            interval = f"{interval}h"
            info(f"Interval normalized to '{interval}'")
        if not re.match(r'^\d+[smhd]$', interval):
            warn(f"Interval '{interval}' may not be valid — expected format: 30m, 1h, 4h, 24h")
        wf_id = f"{namespace}-{WORKFLOW_ID_SUFFIX}-scheduled" if namespace != "default" else f"{WORKFLOW_ID_SUFFIX}-scheduled"
        delete_if_exists(kb_url, hdr, space_path(namespace, f"/api/workflows/workflow/{wf_id}"))
        info(f"Deploying scheduled workflow: {wf_id} (every {interval})")
        yaml = render_template(WORKFLOW_SCHEDULED_TEMPLATE).replace("__SCHEDULE_INTERVAL__", interval)
        deploy_workflow_yaml(kb_url, hdr, namespace, wf_id, yaml,
                             wf_name="ES Cluster Triage Scheduled")
        ok(f"Scheduled workflow deployed: {wf_id} — runs every {interval}")
        deployed_wfs.append(wf_id)

    if wf_choice == "4":
        info("Skipping workflow — agent deployed with no workflow trigger")

    if deployed_wfs:
        installed["workflows"] = deployed_wfs

    # Optional Slack connector
    if deployed_wfs and confirm("Add Slack notifications to the alert workflow?", False):
        slack_webhook = ask_secret("Slack webhook URL")
        if slack_webhook:
            try:
                connector_id = provision_connector(kb_url, hdr, namespace, slack_webhook)
                installed["connector_id"] = connector_id
                ok(f"Slack connector created: {connector_id}")
                info("To add Slack to the workflow: edit it in Kibana and add a Slack step")
                info(f"Use connector ID: {connector_id}")
            except RuntimeError as exc:
                warn(f"Slack connector failed: {exc}")

    # Persist updated manifest
    INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
    INSTALLED_FILE.chmod(0o600)
    log(f"Install manifest updated with workflows: {deployed_wfs}")


# ── [8] Verify ─────────────────────────────────────────────────────────────────
def verify_deployment(creds: dict[str, str], hdr: tuple[str, str], namespace: str) -> None:
    step(8, "Verifying Deployment")

    kb_url = creds["KB_URL"]
    installed_data: dict[str, Any] = {}
    if INSTALLED_FILE.exists():
        try:
            installed_data = json.loads(INSTALLED_FILE.read_text())
        except Exception:
            pass

    agent_id = installed_data.get("agent_id", AGENT_ID)
    agent = get_if_exists(kb_url, hdr, space_path(namespace, f"/api/agent_builder/agents/{agent_id}"))
    if agent:
        ok(f"Agent '{agent_id}' ✓")
    else:
        warn(f"Agent '{agent_id}' not found")

    tool_ids = installed_data.get("tools", [])
    if tool_ids:
        sample = tool_ids[0]
        t = get_if_exists(kb_url, hdr, space_path(namespace, f"/api/agent_builder/tools/{sample}"))
        if t:
            ok(f"{len(tool_ids)} tools deployed ✓")
        else:
            warn(f"Sample tool '{sample}' not found")

    skill_ids = installed_data.get("skills", [])
    if skill_ids:
        ok(f"{len(skill_ids)} skills deployed ✓")

    for wf_id in installed_data.get("workflows", []):
        wf = get_if_exists(kb_url, hdr, space_path(namespace, f"/api/workflows/workflow/{wf_id}"))
        if wf:
            ok(f"Workflow '{wf_id}' ✓")
        else:
            warn(f"Workflow '{wf_id}' not found")


# ── [9] Live agent validation ──────────────────────────────────────────────────
def live_agent_validation(creds: dict[str, str], hdr: tuple[str, str], namespace: str, ds_info: dict[str, str]) -> None:
    step(9, "Live Agent Validation")

    es_url = creds["ES_URL"]
    kb_url = creds["KB_URL"]
    monitoring_ds = ds_info["monitoring_ds"]
    space_url = f"{kb_url}/s/{namespace}" if namespace != "default" else kb_url

    print(f"""
  {c(CYAN, "Run a live query against your monitoring data")}
  This validates that the agent tools can see your cluster data.
  The query runs the {c(BOLD, 'cluster_health_summary')} tool directly against ES.
    """)

    if not confirm("Run live validation query?", True):
        info("Skipping — you can validate manually via the Kibana agent URL below")
    else:
        query = (
            f"FROM {monitoring_ds}\n"
            "| WHERE @timestamp >= NOW() - 1 hour\n"
            "| WHERE metricset.name == \"cluster_stats\"\n"
            "| WHERE elasticsearch.cluster.name IS NOT NULL\n"
            "| STATS last_seen = MAX(@timestamp),\n"
            "        status = MAX(elasticsearch.cluster.stats.status),\n"
            "        nodes = MAX(elasticsearch.cluster.stats.nodes.count),\n"
            "        shards = MAX(elasticsearch.cluster.stats.indices.shards.count),\n"
            "        max_heap = MAX(elasticsearch.node.stats.jvm.mem.heap.used.pct)\n"
            "  BY elasticsearch.cluster.name\n"
            "| SORT last_seen DESC\n"
            "| LIMIT 10"
        )

        print(f"\n  {c(DIM, 'Querying:')} {monitoring_ds}")
        try:
            result = es_request(
                es_url, hdr, "POST", "/_query",
                body={"query": query},
                timeout=30,
            )
            columns = [col.get("name", "") for col in result.get("columns", [])]
            values = result.get("values", [])

            if not values:
                warn("No monitoring data found — ensure Stack Monitoring is sending data")
            else:
                print(f"\n  {c(GREEN + BOLD, 'Cluster Status (last 1 hour):')}\n")

                # Column widths
                col_w = {
                    "elasticsearch.cluster.name": 30,
                    "status": 10,
                    "nodes": 7,
                    "shards": 8,
                    "max_heap": 10,
                    "last_seen": 24,
                }
                display_cols = ["elasticsearch.cluster.name", "status", "nodes", "shards", "max_heap"]
                display_cols = [c_name for c_name in display_cols if c_name in columns]

                def _hdr(cn: str) -> str:
                    w = col_w.get(cn, 15)
                    label = cn.split(".")[-1].upper()[:w]
                    return c(DIM, f"{label:<{w}}")
                header = "  " + "  ".join(_hdr(cn) for cn in display_cols)
                print(header)
                print("  " + c(DIM, "─" * 60))

                for row in values:
                    row_map = dict(zip(columns, row))
                    parts = []
                    for cn in display_cols:
                        val = row_map.get(cn, "—")
                        w = col_w.get(cn, 15)
                        if cn == "status":
                            sv = str(val or "—")[:w]
                            color = GREEN if val == "green" else (YELLOW if val == "yellow" else RED)
                            parts.append("  " + c(color, f"{sv:<{w}}"))
                        elif cn == "max_heap":
                            try:
                                hp = float(val or 0)
                                hp = hp / 100 if hp > 100 else hp
                                hps = f"{hp:.1f}%"
                                color = RED if hp > 85 else (YELLOW if hp > 70 else GREEN)
                                parts.append("  " + c(color, f"{hps:<{w}}"))
                            except (TypeError, ValueError):
                                parts.append(f"  {'—':<{w}}")
                        else:
                            sv = str(val or "—")[:w]
                            parts.append(f"  {sv:<{w}}")
                    print("  " + "".join(parts))

                print()
                ok(f"Live data validated — {len(values)} cluster(s) found in monitoring stream")

                # Prompt user for custom query
                print(f"\n  {c(CYAN, 'Try a custom query (optional)')}")
                print(f"  {c(DIM, 'You can enter any ES|QL query to test. Leave blank to skip.')}")
                custom_q = ask("ES|QL query (blank to skip)").strip()
                if custom_q:
                    print(f"\n  {c(DIM, 'Running…')}")
                    try:
                        custom_result = es_request(
                            es_url, hdr, "POST", "/_query",
                            body={"query": custom_q},
                            timeout=30,
                        )
                        ccols = [col.get("name") for col in custom_result.get("columns", [])]
                        cvals = custom_result.get("values", [])
                        print(f"\n  {c(GREEN, 'Result:')} {len(cvals)} row(s) — columns: {', '.join(ccols)}")
                        for row in cvals[:5]:
                            row_map = dict(zip(ccols, row))
                            print(f"  {c(DIM, str(row_map)[:120])}")
                    except RuntimeError as exc:
                        warn(f"Query error: {exc}")

        except RuntimeError as exc:
            if "index_not_found" in str(exc).lower():
                warn("Monitoring index not found — Stack Monitoring may not be enabled")
            else:
                warn(f"Validation query failed: {exc}")

    # Show Kibana agent URL
    agent_url = f"{space_url}/app/agent_builder"
    print(f"""
  {c(CYAN + BOLD, "Open your agent in Kibana:")}
  {c(BOLD, agent_url)}

  {c(DIM, 'You can chat with the agent directly in Kibana Agent Builder.')}
  {c(DIM, 'Example questions:')}
  {c(DIM, '  · "What is the current cluster health?"')}
  {c(DIM, '  · "Are there any indexing failures in the last hour?"')}
  {c(DIM, '  · "Show disk pressure across all clusters"')}
    """)


# ── Final summary ──────────────────────────────────────────────────────────────
def print_summary(creds: dict[str, str], namespace: str, ds_info: dict[str, str]) -> None:
    installed_data: dict[str, Any] = {}
    if INSTALLED_FILE.exists():
        try:
            installed_data = json.loads(INSTALLED_FILE.read_text())
        except Exception:
            pass

    kb_url = creds["KB_URL"]
    space_url = f"{kb_url}/s/{namespace}" if namespace != "default" else kb_url
    wfs = installed_data.get("workflows", [])

    print(f"""
  {c(GREEN + BOLD, "╔══ Installation Complete ══════════════════════════════════╗")}

  {c(CYAN, "Agent URL:")}   {c(BOLD, space_url)}/app/agent_builder
  {c(CYAN, "Space:")}       {namespace}
  {c(CYAN, "Tools:")}       {len(installed_data.get('tools', []))} ES|QL triage tools
  {c(CYAN, "Skills:")}      {len(installed_data.get('skills', []))} skill groups
  {c(CYAN, "Agent:")}       {installed_data.get('agent_id', AGENT_ID)}
  {c(CYAN, "Workflows:")}   {', '.join(wfs) if wfs else 'none (agent only)'}
  {c(CYAN, "Monitoring:")}  {ds_info['monitoring_ds']} → alias: {ds_info['monitoring_alias']}
  {c(CYAN, "Logs:")}        {ds_info['log_ds']} → alias: {ds_info['log_alias']}

  {c(CYAN, "Local files:")}
  · {INSTALLED_FILE}
  · {CREDS_FILE}
  · {LOG_FILE}

  {c(YELLOW, "Next steps:")}
  1. Open the agent in Kibana and ask a triage question
  2. Connect the alert workflow to a Kibana monitoring rule
  3. Run {c(BOLD, 'python3 install/verify.py')} to re-check deployment
  4. Run {c(BOLD, 'python3 install/uninstall.py')} to remove everything
    """)


# ── Optional: MCP app ──────────────────────────────────────────────────────────
def offer_mcp_app() -> None:
    mcp_dir = ROOT / "mcp-app"
    if not mcp_dir.exists():
        return

    print(c(DIM, "  ─" * 30))
    print(f"\n  {c(CYAN + BOLD, 'Optional: MCP Desktop App')}")
    print(f"  {c(DIM, 'Provides rich visual dashboards (gauges, charts) inside Claude Desktop.')}")

    if not confirm("Build the MCP app now?", False):
        info("To build later: cd mcp-app && npm install && npm run build")
        return

    import subprocess
    info("Running npm install…")
    r = subprocess.run(["npm", "install"], cwd=str(mcp_dir), capture_output=True, text=True)
    if r.returncode != 0:
        err(f"npm install failed: {r.stderr[:200]}")
        return
    ok("npm install complete")

    info("Building MCP app…")
    r = subprocess.run(["npm", "run", "build"], cwd=str(mcp_dir), capture_output=True, text=True)
    if r.returncode != 0:
        err(f"Build failed: {r.stderr[:200]}")
        return
    ok("Build complete")

    mcpb = list(mcp_dir.glob("*.mcpb"))
    if mcpb:
        ok(f"MCP app ready: {mcpb[0].name}")
        print(f"\n  {c(CYAN, 'Install in Claude Desktop:')} Settings → Extensions → Add Extension")
        print(f"  {c(DIM, str(mcpb[0]))}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> int:
    open_log()
    print_banner()

    print(c(DIM, "  Deploys the Elasticsearch Cluster Triage Agent into any Kibana space."))
    print(c(DIM, "  Credentials are stored locally and never displayed."))
    print(c(DIM, f"  Install log: {LOG_FILE}"))

    if not confirm("\nReady to begin?", True):
        print("  Aborted.")
        return 0

    show_api_key_guide()
    if not confirm("Credentials ready to proceed?", True):
        print("  Run this installer again when your credentials are ready.")
        return 0

    try:
        check_prerequisites()
        creds = collect_credentials()
        hdr = build_auth_header(creds)
        validate_auth(creds, hdr)
        namespace = collect_namespace(creds, hdr)
        ds_info = collect_and_validate_datastreams(creds, hdr)
        save_credentials(creds, namespace, ds_info)
        installed = deploy_all(creds, hdr, namespace, ds_info)
        deploy_workflows(creds, hdr, namespace, ds_info, installed)
        verify_deployment(creds, hdr, namespace)
        live_agent_validation(creds, hdr, namespace, ds_info)
        print_summary(creds, namespace, ds_info)
        offer_mcp_app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print(f"\n\n  {c(YELLOW, 'Installation interrupted.')}")
        return 1
    except RuntimeError as exc:
        print(f"\n\n  {c(RED + BOLD, 'Installation failed:')}")
        print(f"  {c(RED, str(exc))}")
        print(f"  {c(DIM, f'See log: {LOG_FILE}')}")
        log(f"FATAL: {exc}")
        return 1
    finally:
        close_log()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
