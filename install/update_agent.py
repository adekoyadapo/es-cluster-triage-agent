#!/usr/bin/env python3
"""
update_agent.py — push an updated agent.json to the live cluster.

Reads cluster context from .credentials.local and .installed.json.
Only updates the agent definition — tools and skills are untouched.

Usage:
    export ES_API_KEY="<id>:<secret>"
    python3 install/update_agent.py [--agent app-index-triage-agent]

    Default: updates both agents recorded in .installed.json.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parent
ROOT = INSTALL_DIR.parent
CREDS_FILE = INSTALL_DIR / ".credentials.local"
INSTALLED_FILE = INSTALL_DIR / ".installed.json"

AGENT_DIRS = {
    "es-cluster-triage-agent": ROOT / "kibana-agent-builder" / "es-cluster-triage" / "agent.json",
    "app-index-triage-agent":  ROOT / "kibana-agent-builder" / "app-index-triage" / "agent.json",
}

R = "\033[0m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"
CYAN = "\033[36m"; DIM = "\033[2m"; BOLD = "\033[1m"
def ok(m):   print(f"  {GREEN}✓{R}  {m}")
def warn(m): print(f"  {YELLOW}⚠{R}  {m}")
def err(m):  print(f"  {RED}✗{R}  {m}")
def info(m): print(f"  {DIM}·{R}  {m}")


def kb_req(base: str, auth: tuple, method: str, path: str, body=None, timeout=30) -> Any:
    payload = None if body is None else json.dumps(body).encode()
    req = Request(f"{base}{path}", data=payload, method=method.upper(),
                  headers={"Accept": "application/json", "Content-Type": "application/json",
                           "kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana",
                           auth[0]: auth[1]})
    try:
        with urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            return json.loads(text) if text else {}
    except HTTPError as e:
        body_text = e.read().decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body_text[:300]}") from e
    except URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}") from e


def get_if_exists(base, auth, path):
    try:
        return kb_req(base, auth, "GET", path)
    except RuntimeError as e:
        if "HTTP 404" in str(e) or "HTTP 403" in str(e):
            return None
        raise


def space_path(ns: str, path: str) -> str:
    return path if ns == "default" else f"/s/{ns}{path}"


def deploy_agent(kb_url: str, auth: tuple, namespace: str,
                 agent_id: str, agent_data: dict) -> bool:
    agent_path = space_path(namespace, f"/api/agent_builder/agents/{agent_id}")
    agents_path = space_path(namespace, "/api/agent_builder/agents")

    # Try PUT first (update in place without disrupting the agent)
    existing = get_if_exists(kb_url, auth, agent_path)
    if existing:
        try:
            kb_req(kb_url, auth, "PUT", agent_path, body=agent_data)
            ok(f"Agent updated via PUT: {agent_id}")
            return True
        except RuntimeError as e:
            if "HTTP 405" in str(e) or "HTTP 404" in str(e):
                pass  # PUT not supported — fall through to DELETE+POST
            elif "HTTP 403" in str(e):
                warn(f"Insufficient privilege to update {agent_id}")
                return False
            else:
                warn(f"PUT failed for {agent_id}: {e} — falling back to DELETE+POST")

    # DELETE + POST
    try:
        kb_req(kb_url, auth, "DELETE", agent_path)
        info(f"Deleted existing agent: {agent_id}")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            warn(f"Delete failed: {e}")

    try:
        kb_req(kb_url, auth, "POST", agents_path, body=agent_data)
        ok(f"Agent deployed: {agent_id}")
        return True
    except RuntimeError as e:
        if "HTTP 409" in str(e):
            warn(f"Agent {agent_id} already exists (409) — it may not have been deleted cleanly")
        else:
            err(f"Failed to deploy {agent_id}: {e}")
        return False


def main() -> int:
    api_key = os.environ.get("ES_API_KEY", "").strip()
    if not api_key:
        err("ES_API_KEY environment variable not set.")
        print(f"  Set it:  export ES_API_KEY=\"<id>:<secret>\"")
        return 1

    if not CREDS_FILE.exists() or not INSTALLED_FILE.exists():
        err("No saved install found. Run the full installer first.")
        return 1

    creds = json.loads(CREDS_FILE.read_text())
    installed = json.loads(INSTALLED_FILE.read_text())
    kb_url = creds["kb_url"]
    namespace = installed.get("namespace", "default")

    if ":" in api_key and not api_key.startswith("="):
        auth = ("Authorization", f"ApiKey {base64.b64encode(api_key.encode()).decode()}")
    else:
        auth = ("Authorization", f"ApiKey {api_key}")

    # Parse --agent flag
    target_agents: list[str] = []
    args = sys.argv[1:]
    if "--agent" in args:
        idx = args.index("--agent")
        if idx + 1 < len(args):
            target_agents = [args[idx + 1]]
    if not target_agents:
        # Default: all agents recorded in installed.json
        target_agents = []
        if agent_id := installed.get("agent_id"):
            target_agents.append(agent_id)
        target_agents.extend(installed.get("optional_agents", []))

    if not target_agents:
        warn("No agents found in .installed.json")
        return 1

    print(f"\n  {CYAN}{BOLD}Agent update{R}")
    print(f"  {DIM}Kibana:    {kb_url}{R}")
    print(f"  {DIM}Namespace: {namespace}{R}")
    print(f"  {DIM}Agents:    {target_agents}{R}\n")

    # Validate connectivity
    try:
        kb_req(kb_url, auth, "GET", "/api/status")
        ok("Kibana connected")
    except RuntimeError as e:
        err(f"Cannot reach Kibana: {e}")
        return 1

    deployed = 0
    for agent_id in target_agents:
        agent_file = AGENT_DIRS.get(agent_id)
        if not agent_file or not agent_file.exists():
            warn(f"No local agent.json found for '{agent_id}' — skipping")
            continue

        info(f"Updating: {agent_id}")
        agent_data = json.loads(agent_file.read_text())

        # The optional agent may live in a different namespace
        tgt_ns = installed.get("optional_namespace", namespace) if "app-index" in agent_id else namespace
        if deploy_agent(kb_url, auth, tgt_ns, agent_id, agent_data):
            deployed += 1

    print()
    ok(f"{deployed}/{len(target_agents)} agent(s) updated.")
    sp = f"/s/{namespace}" if namespace != "default" else ""
    print(f"\n  View in Kibana: {kb_url}{sp}/app/agent_builder\n")
    return 0 if deployed == len(target_agents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
