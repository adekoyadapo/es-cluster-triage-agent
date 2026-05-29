#!/usr/bin/env python3
"""
Elasticsearch Cluster Triage Agent — Deployment Verifier
=========================================================
Health-checks the deployment against what was installed.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parent
INSTALLED_FILE = INSTALL_DIR / ".installed.json"
CREDS_FILE = INSTALL_DIR / ".credentials.local"

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
WHITE = "\033[97m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{R}"


def ok(msg: str) -> None:
    print(f"  {c(GREEN, '✓')} {msg}")


def warn(msg: str) -> None:
    print(f"  {c(YELLOW, '⚠')}  {msg}")


def err(msg: str) -> None:
    print(f"  {c(RED, '✗')} {msg}")


def info(msg: str) -> None:
    print(f"  {c(DIM, '·')} {msg}")


def kibana_request(
    base_url: str, auth_hdr: tuple[str, str], method: str, path: str, timeout: int = 15
) -> Any:
    url = f"{base_url}{path}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "kbn-xsrf": "true",
            "x-elastic-internal-origin": "Kibana",
            auth_hdr[0]: auth_hdr[1],
        },
        method=method.upper(),
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return json.loads(text) if text else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def es_request(
    es_url: str, auth_hdr: tuple[str, str], method: str, path: str,
    body: dict[str, Any] | None = None, timeout: int = 15
) -> Any:
    import json as _j
    payload = None if body is None else _j.dumps(body).encode()
    url = f"{es_url}{path}"
    req = Request(
        url, data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            auth_hdr[0]: auth_hdr[1],
        },
        method=method.upper(),
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return json.loads(text) if text else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def space_path(space_id: str, path: str) -> str:
    return path if space_id == "default" else f"/s/{space_id}{path}"


def build_auth_from_creds(creds_data: dict[str, Any]) -> tuple[str, str]:
    if creds_data.get("auth_type") == "basic":
        un = creds_data.get("username", "")
        pw = creds_data.get("password", "")
        if un and pw:
            token = base64.b64encode(f"{un}:{pw}".encode()).decode()
            return ("Authorization", f"Basic {token}")
    api_key = creds_data.get("api_key", "")
    if api_key:
        if ":" in api_key:
            encoded = base64.b64encode(api_key.encode()).decode()
            return ("Authorization", f"ApiKey {encoded}")
        return ("Authorization", f"ApiKey {api_key}")
    raise RuntimeError("No credentials available. Run install.py first.")


def main() -> int:
    print()
    print(c(CYAN, "╔══════════════════════════════════════════════════════════════╗"))
    print(c(CYAN, "║") + c(BOLD + WHITE, "  Elasticsearch Cluster Triage Agent — Deployment Verifier  ") + c(CYAN, "  ║"))
    print(c(CYAN, "╚══════════════════════════════════════════════════════════════╝"))
    print()

    if not INSTALLED_FILE.exists():
        err("No install manifest found. Run install.py first.")
        return 1

    try:
        installed = json.loads(INSTALLED_FILE.read_text())
    except Exception as exc:
        err(f"Could not read install manifest: {exc}")
        return 1

    creds_data: dict[str, Any] = {}
    if CREDS_FILE.exists():
        try:
            creds_data = json.loads(CREDS_FILE.read_text())
        except Exception:
            pass

    try:
        hdr = build_auth_from_creds(creds_data)
    except RuntimeError as exc:
        # Prompt for credentials if missing
        import getpass
        print(f"  {c(YELLOW, 'Credentials not found in .credentials.local')}")
        try:
            api_key = getpass.getpass(f"  {c(BOLD, 'API Key')}: ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if ":" in api_key:
            encoded = base64.b64encode(api_key.encode()).decode()
            hdr = ("Authorization", f"ApiKey {encoded}")
        else:
            hdr = ("Authorization", f"ApiKey {api_key}")

    namespace = installed.get("namespace", "es-cluster-triage")
    kb_url = installed.get("kb_url", "")
    es_url = installed.get("es_url") or creds_data.get("es_url", "")
    monitoring_ds = creds_data.get("monitoring_ds", ".monitoring-es-*")

    print(f"  {c(CYAN, 'Installed at:')} {installed.get('installed_at', '?')}")
    print(f"  {c(CYAN, 'Kibana:')}       {kb_url}")
    print(f"  {c(CYAN, 'Space:')}        {namespace}")
    print()

    # ── Kibana connectivity ────────────────────────────────────────────────────
    print(c(BOLD, "  [1/5] Kibana Connectivity"))
    try:
        status = kibana_request(kb_url, hdr, "GET", "/api/status")
        lvl = status.get("status", {}).get("overall", {}).get("level", "unknown")
        ok(f"Kibana reachable — status: {lvl}")
    except RuntimeError as exc:
        err(f"Kibana unreachable: {exc}")
        return 1

    # ── Agent ─────────────────────────────────────────────────────────────────
    print(c(BOLD, "\n  [2/5] Agent"))
    agent_id = installed.get("agent_id", "es-cluster-triage-agent")
    try:
        agent = kibana_request(kb_url, hdr, "GET", space_path(namespace, f"/api/agent_builder/agents/{agent_id}"))
        ok(f"Agent '{agent_id}' exists")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            err(f"Agent '{agent_id}' NOT FOUND — reinstall may be needed")
        else:
            err(f"Agent check failed: {exc}")

    # ── Tools ─────────────────────────────────────────────────────────────────
    print(c(BOLD, "\n  [3/5] Tools"))
    tool_ids = installed.get("tools", [])
    missing_tools = []
    for tid in tool_ids:
        try:
            kibana_request(kb_url, hdr, "GET", space_path(namespace, f"/api/agent_builder/tools/{tid}"))
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                missing_tools.append(tid)
            else:
                warn(f"Could not check tool '{tid}': {exc}")

    if not missing_tools:
        ok(f"All {len(tool_ids)} tools present")
    else:
        err(f"{len(missing_tools)} tools missing: {missing_tools[:3]}{'...' if len(missing_tools)>3 else ''}")

    # ── Skills ────────────────────────────────────────────────────────────────
    print(c(BOLD, "\n  [4/5] Skills"))
    skill_ids = installed.get("skills", [])
    missing_skills = []
    for sid in skill_ids:
        try:
            kibana_request(kb_url, hdr, "GET", space_path(namespace, f"/api/agent_builder/skills/{sid}"))
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                missing_skills.append(sid)

    if not missing_skills:
        ok(f"All {len(skill_ids)} skills present")
    else:
        err(f"{len(missing_skills)} skills missing: {missing_skills}")

    # ── Workflow ───────────────────────────────────────────────────────────────
    print(c(BOLD, "\n  [5/5] Workflow & Data"))
    workflow_id = installed.get("workflow_id", "")
    if workflow_id:
        try:
            wf = kibana_request(kb_url, hdr, "GET", space_path(namespace, f"/api/workflows/workflow/{workflow_id}"))
            wf_name = wf.get("name", workflow_id)
            ok(f"Workflow '{wf_name}' present")
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                warn(f"Workflow '{workflow_id}' not found")
            else:
                warn(f"Workflow check: {exc}")

    # ES|QL data check
    if es_url and monitoring_ds:
        try:
            result = es_request(
                es_url, hdr, "POST", "/_query",
                body={"query": f"FROM {monitoring_ds} | LIMIT 1 | KEEP @timestamp"},
            )
            cols = [col.get("name") for col in result.get("columns", [])]
            if "@timestamp" in cols:
                ok(f"Monitoring data accessible via {monitoring_ds}")
            else:
                warn(f"ES|QL returned unexpected shape from {monitoring_ds}")
        except RuntimeError as exc:
            if "index_not_found" in str(exc).lower():
                warn(f"No monitoring data yet at '{monitoring_ds}' — enable Stack Monitoring")
            else:
                warn(f"ES|QL check: {exc}")

    print(f"\n  {c(GREEN + BOLD, 'Verification complete.')}")
    space_url = f"{kb_url}/s/{namespace}" if namespace != "default" else kb_url
    print(f"  {c(CYAN, 'Kibana agent page:')} {space_url}/app/agent_builder\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
