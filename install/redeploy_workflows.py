#!/usr/bin/env python3
"""
redeploy_workflows.py — re-deploy all workflow YAMLs to an existing installation.

Reads cluster context from .credentials.local and .installed.json.
Requires the API key via the ES_API_KEY environment variable (no interactive prompts).

Usage:
    export ES_API_KEY="<id>:<secret>"   # or base64-encoded key
    python3 install/redeploy_workflows.py

Optional overrides (env vars):
    CASE_OWNER                 default: observability
    REPORT_INDEX               default: triage-reports
    FIELDDATA_THRESHOLD_BYTES  default: 104857600  (100 MB)
    SEGMENTS_THRESHOLD         default: 50
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INSTALL_DIR = Path(__file__).resolve().parent
ROOT = INSTALL_DIR.parent
CREDS_FILE = INSTALL_DIR / ".credentials.local"
INSTALLED_FILE = INSTALL_DIR / ".installed.json"
LOG_FILE = INSTALL_DIR / "install.log"

WORKFLOW_TEMPLATES = {
    "es-cluster-triage": ROOT / "workflows" / "es-cluster-triage.workflow.yaml",
    "es-cluster-triage-scheduled": ROOT / "workflows" / "es-cluster-triage-scheduled.workflow.yaml",
    "app-index-triage": ROOT / "workflows" / "app-index-triage.workflow.yaml",
    "app-index-triage-scheduled": ROOT / "workflows" / "app-index-triage-scheduled.workflow.yaml",
}

# ── Colours ────────────────────────────────────────────────────────────────────
R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; CYAN = "\033[36m"

def ok(m):  print(f"  {GREEN}✓{R}  {m}")
def warn(m): print(f"  {YELLOW}⚠{R}  {m}")
def err(m):  print(f"  {RED}✗{R}  {m}")
def info(m): print(f"  {DIM}·{R}  {m}")

_lf = None
def _log(m):
    if _lf:
        _lf.write(m + "\n"); _lf.flush()

# ── HTTP ───────────────────────────────────────────────────────────────────────
def kb_req(base: str, hdr: tuple, method: str, path: str, *, body=None) -> Any:
    payload = None if body is None else json.dumps(body).encode()
    req = Request(f"{base}{path}", data=payload, method=method.upper(),
                  headers={"Accept": "application/json", "Content-Type": "application/json",
                           "kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana",
                           hdr[0]: hdr[1]})
    _log(f"  {method} {base}{path}")
    try:
        with urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
            data = json.loads(text) if text else {}
            _log(f"  → {r.status}")
            return data
    except HTTPError as e:
        body_text = e.read().decode("utf-8", "replace") if e.fp else ""
        _log(f"  → HTTP {e.code}: {body_text[:300]}")
        raise RuntimeError(f"HTTP {e.code} for {method} {path}: {body_text[:300]}") from e
    except URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}") from e

def es_req(base: str, hdr: tuple, method: str, path: str, *, body=None) -> Any:
    payload = None if body is None else json.dumps(body).encode()
    req = Request(f"{base}{path}", data=payload, method=method.upper(),
                  headers={"Accept": "application/json", "Content-Type": "application/json",
                           hdr[0]: hdr[1]})
    try:
        with urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
            return json.loads(text) if text else {}
    except HTTPError as e:
        body_text = e.read().decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} for {method} {path}: {body_text[:300]}") from e
    except URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}") from e

def get_if_exists(base: str, hdr: tuple, path: str) -> Any:
    try:
        return kb_req(base, hdr, "GET", path)
    except RuntimeError as e:
        if "HTTP 404" in str(e) or "HTTP 403" in str(e):
            return None
        raise

def space_path(ns: str, path: str) -> str:
    return path if ns == "default" else f"/s/{ns}{path}"

# ── Core deploy ────────────────────────────────────────────────────────────────
def deploy_workflow(kb_url: str, hdr: tuple, namespace: str,
                    wf_id: str, yaml_text: str, wf_name: str) -> bool:
    """DELETE → sleep → POST → (409?) GET → PUT or retry POST."""
    wf_path = space_path(namespace, f"/api/workflows/workflow/{wf_id}")

    try:
        kb_req(kb_url, hdr, "DELETE", wf_path)
        _log(f"  Pre-deleted {wf_id}")
    except RuntimeError as e:
        if "HTTP 403" in str(e):
            warn(f"Insufficient privilege to delete {wf_id} — skipping")
            return False
        # 404 = doesn't exist yet, fine

    time.sleep(2)

    body = {"id": wf_id, "yaml": yaml_text, "name": wf_name}
    try:
        kb_req(kb_url, hdr, "POST", space_path(namespace, "/api/workflows/workflow"), body=body)
        ok(f"Workflow deployed: {wf_name} ({wf_id})")
        return True
    except RuntimeError as e:
        if "HTTP 403" in str(e):
            warn(f"Insufficient privilege to create {wf_id}")
            return False
        if "HTTP 409" not in str(e):
            raise

    # 409: check if live record exists
    existing = get_if_exists(kb_url, hdr, wf_path)
    if existing:
        put_body = {"yaml": yaml_text, "enabled": True, "name": wf_name}
        try:
            kb_req(kb_url, hdr, "PUT", wf_path, body=put_body)
            ok(f"Workflow updated: {wf_name} ({wf_id})")
            return True
        except RuntimeError as put_e:
            if "HTTP 403" in str(put_e):
                warn(f"Insufficient privilege to update {wf_id}")
                return False
            warn(f"PUT failed for {wf_id}: {put_e}")
            return False
    else:
        # Tombstone race — retry POST after sleep
        _log(f"  Tombstone race for {wf_id}, sleeping 3s then retrying")
        time.sleep(3)
        try:
            kb_req(kb_url, hdr, "POST", space_path(namespace, "/api/workflows/workflow"), body=body)
            ok(f"Workflow deployed (retry): {wf_name} ({wf_id})")
            return True
        except RuntimeError as retry_e:
            warn(f"Deploy failed after retry for {wf_id}: {retry_e}")
            return False

# ── Render ─────────────────────────────────────────────────────────────────────
def strip_slack(yaml_text: str) -> str:
    """Replace route_notification switch with a silent console step."""
    lines = yaml_text.splitlines()
    out: list[str] = []
    inside = False
    prefix = "  - name: route_notification"
    for line in lines:
        if not inside and line.startswith(prefix):
            out += ["  - name: route_notification", "    type: console",
                    "    with:", "      message: \"No Slack connector configured.\""]
            inside = True
            continue
        if inside:
            if line.startswith("  - name: ") and not line.startswith(prefix):
                inside = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out)

def render(tmpl_path: Path, monitoring_ds: str, agent_id: str,
           connector_id: str, interval: str,
           case_owner: str, report_index: str,
           fielddata_thresh: str, segments_thresh: str) -> str:
    text = tmpl_path.read_text()
    text = text.replace("__METRICS_PATTERN__", monitoring_ds)
    text = text.replace("__AGENT_ID__", agent_id)
    text = text.replace("__CASE_OWNER__", case_owner)
    text = text.replace("__REPORT_INDEX__", report_index)
    text = text.replace("__FIELDDATA_THRESHOLD_BYTES__", fielddata_thresh)
    text = text.replace("__SEGMENTS_THRESHOLD__", segments_thresh)
    text = text.replace("__SCHEDULE_INTERVAL__", interval)
    if connector_id:
        text = text.replace("__SLACK_CONNECTOR_ID__", connector_id)
    else:
        text = strip_slack(text)
    unresolved = re.findall(r'__[A-Z_]+__', text)
    if unresolved:
        warn(f"Unresolved placeholders in {tmpl_path.name}: {unresolved}")
    return text

# ── triage-reports index ───────────────────────────────────────────────────────
def create_triage_reports_index(es_url: str, hdr: tuple, report_index: str) -> None:
    mapping = {
        "settings": {"number_of_replicas": 1},
        "mappings": {"properties": {
            "@timestamp":    {"type": "date"},
            "workflow":      {"type": "keyword"},
            "trigger":       {"type": "keyword"},
            "execution_id":  {"type": "keyword"},
            "alert_name":    {"type": "keyword"},
            "case_id":       {"type": "keyword"},
            "case_appended": {"type": "boolean"},
            "triage": {"properties": {
                "severity":         {"type": "keyword"},
                "confidence":       {"type": "keyword"},
                "headline":         {"type": "text"},
                "root_cause":       {"type": "text"},
                "summary_markdown": {"type": "text"},
                "impacted_cluster": {"type": "keyword"},
                "impacted_index":   {"type": "keyword"},
                "evidence":         {"type": "text"},
                "remediation":      {"type": "text"},
            }},
        }},
    }
    try:
        es_req(es_url, hdr, "PUT", f"/{report_index}", body=mapping)
        ok(f"{report_index} index created with explicit mappings")
    except RuntimeError as e:
        if "resource_already_exists_exception" in str(e).lower():
            ok(f"{report_index} index already exists")
        else:
            warn(f"Could not create {report_index}: {e} — will be auto-created on first run")

# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> int:
    global _lf
    _lf = open(LOG_FILE, "a", encoding="utf-8")

    api_key = os.environ.get("ES_API_KEY", "").strip()
    if not api_key:
        err("ES_API_KEY environment variable not set.")
        print(f"  Set it before running:  export ES_API_KEY=\"<id>:<secret>\"")
        return 1

    if not CREDS_FILE.exists() or not INSTALLED_FILE.exists():
        err("No saved install found. Run the full installer first.")
        return 1

    creds_data = json.loads(CREDS_FILE.read_text())
    installed  = json.loads(INSTALLED_FILE.read_text())

    kb_url      = creds_data["kb_url"]
    es_url      = creds_data.get("es_url", "")
    namespace   = installed.get("namespace", creds_data.get("namespace", "default"))
    monitoring_ds = creds_data.get("monitoring_ds", ".monitoring-es-*")
    agent_id    = installed.get("agent_id", "es-cluster-triage-agent")
    connector_id = installed.get("connector_id", "")

    # Env-var overrides
    case_owner      = os.environ.get("CASE_OWNER", "observability")
    report_index    = os.environ.get("REPORT_INDEX", "triage-reports")
    fielddata_thresh = os.environ.get("FIELDDATA_THRESHOLD_BYTES", "104857600")
    segments_thresh  = os.environ.get("SEGMENTS_THRESHOLD", "50")
    interval         = os.environ.get("SCHEDULE_INTERVAL", "1h")

    # Build auth header
    import base64
    if ":" in api_key and not api_key.startswith("="):
        # id:secret form
        hdr = ("Authorization", f"ApiKey {base64.b64encode(api_key.encode()).decode()}")
    else:
        hdr = ("Authorization", f"ApiKey {api_key}")

    print(f"\n  {CYAN}{BOLD}Workflow-only redeploy{R}")
    print(f"  {DIM}KB:        {kb_url}{R}")
    print(f"  {DIM}Namespace: {namespace}{R}")
    print(f"  {DIM}Agent:     {agent_id}{R}")
    print(f"  {DIM}Connector: {connector_id or '(none — Slack steps will be removed)'}{R}")
    print(f"  {DIM}Interval:  {interval}{R}")
    print()

    # Validate connectivity
    try:
        kb_req(kb_url, hdr, "GET", "/api/status")
        ok("Kibana connected")
    except RuntimeError as e:
        err(f"Cannot reach Kibana: {e}")
        return 1

    # Create triage-reports index
    if es_url:
        create_triage_reports_index(es_url, hdr, report_index)

    # Build the list of workflows to deploy
    # Each entry: (workflow_id, template_path, display_name)
    opt_namespace = installed.get("optional_namespace", namespace)
    opt_agent_id  = (installed.get("optional_agents") or [agent_id])[0]
    opt_connector = installed.get("optional_connector_id", connector_id)

    workflows_to_deploy: list[tuple[str, Path, str, str, str]] = []
    for wf_id in installed.get("workflows", []):
        # Determine which template by suffix
        suffix = wf_id.replace(f"{namespace}-", "").replace(f"es-triage-", "es-cluster-triage-")
        # Map known suffixes to template files
        if "scheduled" in wf_id:
            tmpl = WORKFLOW_TEMPLATES.get("es-cluster-triage-scheduled")
            name = "ES Cluster Triage Scheduled"
        else:
            tmpl = WORKFLOW_TEMPLATES.get("es-cluster-triage")
            name = "ES Cluster Triage Summary"
        if tmpl:
            workflows_to_deploy.append((wf_id, tmpl, name, agent_id, connector_id))

    for wf_id in installed.get("optional_workflows", []):
        if "scheduled" in wf_id:
            tmpl = WORKFLOW_TEMPLATES.get("app-index-triage-scheduled")
            name = "Application Index Triage Scheduled"
        else:
            tmpl = WORKFLOW_TEMPLATES.get("app-index-triage")
            name = "Application Index Triage Summary"
        if tmpl:
            workflows_to_deploy.append((wf_id, tmpl, name, opt_agent_id, opt_connector))

    if not workflows_to_deploy:
        warn("No workflows recorded in .installed.json — nothing to redeploy.")
        warn("Run the full installer to set up workflows first.")
        return 1

    print(f"  Deploying {len(workflows_to_deploy)} workflow(s) to namespace '{namespace}'…\n")
    deployed = 0
    for wf_id, tmpl, name, aid, cid in workflows_to_deploy:
        info(f"Deploying: {name} ({wf_id})")
        yaml_text = render(tmpl, monitoring_ds, aid, cid, interval,
                           case_owner, report_index, fielddata_thresh, segments_thresh)
        tgt_ns = opt_namespace if "app-index" in wf_id else namespace
        if deploy_workflow(kb_url, hdr, tgt_ns, wf_id, yaml_text, name):
            deployed += 1

    print()
    ok(f"{deployed}/{len(workflows_to_deploy)} workflows deployed.")
    sp = f"/s/{namespace}" if namespace != "default" else ""
    print(f"\n  View in Kibana: {kb_url}{sp}/app/workflows\n")
    _lf.close()
    return 0 if deployed == len(workflows_to_deploy) else 1


if __name__ == "__main__":
    raise SystemExit(main())
