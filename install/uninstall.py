#!/usr/bin/env python3
"""
Elasticsearch Cluster Triage Agent — Uninstaller
================================================
Removes ONLY what install.py created. Uses .installed.json to track.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parent
INSTALLED_FILE = INSTALL_DIR / ".installed.json"
CREDS_FILE = INSTALL_DIR / ".credentials.local"
LOG_FILE = INSTALL_DIR / "install.log"

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


_log_fp = None


def open_log() -> None:
    global _log_fp
    _log_fp = open(LOG_FILE, "a", encoding="utf-8")
    _log_fp.write(f"\n{'='*60}\n")
    _log_fp.write(f"Uninstall run: {datetime.now().isoformat()}\n")
    _log_fp.write(f"{'='*60}\n")


def log(msg: str) -> None:
    if _log_fp:
        _log_fp.write(msg + "\n")
        _log_fp.flush()


def close_log() -> None:
    if _log_fp:
        _log_fp.close()


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


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        val = input(f"\n  {c(BOLD, prompt)} {c(DIM, hint)}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not val:
        return default
    return val in ("y", "yes")


def kibana_request(
    base_url: str, auth_hdr: tuple[str, str], method: str, path: str, timeout: int = 20
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
    log(f"  {method.upper()} {url}")
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            log(f"  → {resp.status}")
            return json.loads(text) if text else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        log(f"  → HTTP {exc.code}: {body_text[:200]}")
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def es_request(
    es_url: str, auth_hdr: tuple[str, str], method: str, path: str,
    body: dict[str, Any] | None = None, timeout: int = 20
) -> Any:
    import json as _json
    payload = None if body is None else _json.dumps(body).encode("utf-8")
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
            log(f"  → {resp.status}")
            return json.loads(text) if text else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        log(f"  → HTTP {exc.code}: {body_text[:200]}")
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def space_path(space_id: str, path: str) -> str:
    if space_id == "default":
        return path
    return f"/s/{space_id}{path}"


def delete_item(kb_url: str, hdr: tuple[str, str], path: str, label: str) -> None:
    try:
        kibana_request(kb_url, hdr, "DELETE", path)
        ok(f"Removed: {label}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            warn(f"Already gone: {label}")
        else:
            warn(f"Failed to remove {label}: {exc}")


def build_auth_header(creds_data: dict[str, Any]) -> tuple[str, str]:
    auth_type = creds_data.get("auth_type", "apikey")
    if auth_type == "basic":
        username = creds_data.get("username", "")
        password = creds_data.get("password", "")
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            return ("Authorization", f"Basic {token}")

    # Fall back to prompting
    import getpass
    print(f"\n  {c(CYAN, 'Authentication required for uninstall')}")
    print(f"  {c(DIM, '1')} API Key  {c(DIM, '2')} Username / Password")
    try:
        choice = input(f"  {c(BOLD, 'Choice')} [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(0)

    if choice == "2":
        try:
            username = input(f"  {c(BOLD, 'Username')} [elastic]: ").strip() or "elastic"
            password = getpass.getpass(f"  {c(BOLD, 'Password')}: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return ("Authorization", f"Basic {token}")
    else:
        try:
            api_key = getpass.getpass(f"  {c(BOLD, 'API Key')}: ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        if ":" in api_key:
            encoded = base64.b64encode(api_key.encode()).decode()
            return ("Authorization", f"ApiKey {encoded}")
        return ("Authorization", f"ApiKey {api_key}")


def main() -> int:
    open_log()

    print()
    print(c(CYAN, "╔══════════════════════════════════════════════════════════════╗"))
    print(c(CYAN, "║") + c(BOLD + WHITE, "  Elasticsearch Cluster Triage Agent — Uninstaller v1.0     ") + c(CYAN, " ║"))
    print(c(CYAN, "╚══════════════════════════════════════════════════════════════╝"))
    print()

    if not INSTALLED_FILE.exists():
        err(f"Install manifest not found: {INSTALLED_FILE}")
        err("Nothing to uninstall — run install.py first.")
        return 1

    try:
        installed = json.loads(INSTALLED_FILE.read_text())
    except Exception as exc:
        err(f"Could not read install manifest: {exc}")
        return 1

    namespace = installed.get("namespace", "default")
    kb_url = installed.get("kb_url", "")
    es_url = installed.get("es_url", "")
    tool_ids = installed.get("tools", [])
    skill_ids = installed.get("skills", [])
    agent_id = installed.get("agent_id")
    # support both old single workflow_id and new list
    workflow_ids: list[str] = installed.get("workflows") or (
        [installed["workflow_id"]] if installed.get("workflow_id") else []
    )
    connector_id = installed.get("connector_id")
    # Support both old single alias and new dual-alias format
    monitoring_alias = installed.get("monitoring_alias") or installed.get("alias")
    log_alias = installed.get("log_alias")

    print(f"  {c(CYAN, 'Installed at:')} {installed.get('installed_at', 'unknown')}")
    print(f"  {c(CYAN, 'Kibana space:')} {namespace}")
    print(f"  {c(CYAN, 'Kibana URL:')}   {kb_url}")
    print()
    print(f"  Will remove:")
    print(f"  · {len(tool_ids)} tools")
    print(f"  · {len(skill_ids)} skills")
    print(f"  · Agent: {agent_id}")
    for wid in workflow_ids:
        print(f"  · Workflow: {wid}")
    if connector_id:
        print(f"  · Slack connector: {connector_id}")
    if monitoring_alias:
        print(f"  · ES alias: {monitoring_alias}")
    if log_alias and log_alias != "elastic-cloud-logs-8":
        print(f"  · ES alias: {log_alias}")

    if not confirm("Proceed with uninstall?", False):
        print("  Aborted.")
        return 0

    # Build auth header
    creds_data: dict[str, Any] = {}
    if CREDS_FILE.exists():
        try:
            creds_data = json.loads(CREDS_FILE.read_text())
        except Exception:
            pass

    hdr = build_auth_header(creds_data)

    print()
    print(c(BOLD, "  Removing deployment..."))

    # Workflows first
    for wid in workflow_ids:
        delete_item(kb_url, hdr, space_path(namespace, f"/api/workflows/workflow/{wid}"), f"workflow/{wid}")

    # Agent (depends on skills/tools)
    if agent_id:
        delete_item(kb_url, hdr, space_path(namespace, f"/api/agent_builder/agents/{agent_id}"), f"agent/{agent_id}")

    # Skills
    for skill_id in skill_ids:
        delete_item(kb_url, hdr, space_path(namespace, f"/api/agent_builder/skills/{skill_id}"), f"skill/{skill_id}")

    # Tools
    for tool_id in tool_ids:
        delete_item(kb_url, hdr, space_path(namespace, f"/api/agent_builder/tools/{tool_id}"), f"tool/{tool_id}")

    # Connector
    if connector_id:
        delete_item(kb_url, hdr, space_path(namespace, f"/api/actions/connector/{connector_id}"), f"connector/{connector_id}")

    # ES aliases — use POST /_aliases with remove action (DELETE /_alias/{name} is not a valid endpoint)
    for alias_name in filter(None, [monitoring_alias, log_alias if log_alias != "elastic-cloud-logs-8" else None]):
        if alias_name and es_url:
            info(f"Removing ES alias '{alias_name}'...")
            try:
                es_request(es_url, hdr, "POST", "/_aliases", timeout=15, body={
                    "actions": [{"remove": {"index": "*", "alias": alias_name}}]
                })
                ok(f"Alias '{alias_name}' removed")
            except RuntimeError as exc:
                exc_str = str(exc)
                if "HTTP 404" in exc_str or "alias_not_found" in exc_str.lower():
                    warn(f"Alias '{alias_name}' already gone")
                else:
                    warn(f"Alias removal: {exc}")

    # Clean up local install files
    print()
    if confirm("Remove local install files (.installed.json, .credentials.local)?", False):
        for f in [INSTALLED_FILE, CREDS_FILE]:
            if f.exists():
                f.unlink()
                ok(f"Removed {f.name}")

    print(f"""
  {c(GREEN + BOLD, '✓ Uninstall complete')}

  The Elasticsearch Cluster Triage Agent has been removed from:
  · Space: {namespace}
  · Kibana: {kb_url}

  Source files in kibana-agent-builder/ and workflows/ were NOT removed.
  Run install.py to redeploy at any time.
  {c(DIM, f'See log: {LOG_FILE}')}
    """)

    close_log()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
