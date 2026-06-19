#!/usr/bin/env python3
"""
Non-interactive deployment driver.
Reads credentials from values.txt (one KEY="VALUE" per line),
derives ES_URL from KB_URL, then deploys everything without prompts.

Usage:
    python3 install/deploy_from_env.py [path/to/values.txt]

Deploys:
  1. Main es-cluster-triage bundle (tools + skills + agent)
  2. Alert workflow + Scheduled workflow (1h interval)
  3. Slack connector
  4. Optional app-index-triage bundle (same space)
  5. Alert + Scheduled workflows for optional agent
"""
from __future__ import annotations

import json
import re
import sys
import os
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parent
ROOT = INSTALL_DIR.parent
sys.path.insert(0, str(INSTALL_DIR))

import install as _ins


def strip_slack_from_yaml(yaml_text: str) -> str:
    import re
    yaml_text = re.sub(r"\n  slack_connector_id:.*", "", yaml_text)
    yaml_text = re.sub(r"\n  - name: notify_slack\n.*", "", yaml_text, flags=re.DOTALL)
    return yaml_text

DEFAULT_VALUES_FILE = ROOT / "values.txt"


def load_values(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def derive_es_url(kb_url: str) -> str:
    if ".kb." in kb_url:
        return re.sub(r"\.kb\.", ".es.", kb_url)
    raise ValueError(f"Cannot auto-derive ES_URL from KB_URL: {kb_url}")


def build_ds_info(creds: dict[str, str]) -> dict:
    """Replicate the non-interactive core of collect_and_validate_datastreams."""
    es_url = creds["ES_URL"]
    monitoring_ds = ".monitoring-es-*"
    log_ds = "elastic-cloud-logs-8"
    monitoring_alias = "es-monitoring"
    log_alias = "elastic-cloud-logs-8"

    hdr = _ins.build_auth_header(creds)

    _ins.step(5, "Data Source Configuration")
    _ins.info(f"Monitoring pattern: {monitoring_ds}")
    _ins.info(f"Log pattern: {log_ds}")

    # Validate monitoring datastream
    monitoring_doc_count = 0
    try:
        result = _ins.es_request(es_url, hdr, "GET", f"/{monitoring_ds}/_count", timeout=20)
        monitoring_doc_count = result.get("count", 0)
        if monitoring_doc_count > 0:
            _ins.ok(f"Monitoring stream: {monitoring_doc_count:,} documents found")
        else:
            _ins.warn("Monitoring stream has 0 docs — Stack Monitoring may not be enabled")
    except RuntimeError as exc:
        _ins.warn(f"Monitoring validation: {exc}")

    # Create monitoring alias
    monitoring_alias_created = False
    _ins.info(f"Creating alias '{monitoring_alias}' → {monitoring_ds}")
    try:
        _ins.es_request(es_url, hdr, "POST", "/_aliases", body={
            "actions": [{"add": {"index": monitoring_ds, "alias": monitoring_alias, "is_write_index": False}}]
        }, timeout=20)
        _ins.ok(f"Alias '{monitoring_alias}' → {monitoring_ds}")
        monitoring_alias_created = True
    except RuntimeError as exc:
        exc_str = str(exc)
        if "already" in exc_str.lower() or ("HTTP 400" in exc_str and "alias" in exc_str.lower()):
            _ins.ok(f"Alias '{monitoring_alias}' already exists")
            monitoring_alias_created = True
        elif "index_not_found" in exc_str.lower():
            _ins.info(f"No index matching '{monitoring_ds}' — alias skipped")
        elif "HTTP 403" in exc_str:
            _ins.info(f"Alias skipped — API key lacks manage privilege")
        else:
            _ins.info(f"Alias skipped: {exc_str[:120]}")

    # Add Kibana monitoring sibling
    kibana_sibling = re.sub(r"\.monitoring-es", ".monitoring-kibana", monitoring_ds)
    if kibana_sibling != monitoring_ds and monitoring_alias_created:
        try:
            _ins.es_request(es_url, hdr, "POST", "/_aliases", body={
                "actions": [{"add": {"index": kibana_sibling, "alias": monitoring_alias, "is_write_index": False}}]
            }, timeout=15)
            _ins.ok(f"Added Kibana sibling '{kibana_sibling}' to alias")
        except RuntimeError:
            pass

    # Log alias — elastic-cloud-logs-8 is the alias name itself, no creation needed
    log_alias_created = True
    _ins.ok(f"Log pattern is '{log_alias}' — no alias needed")

    return {
        "monitoring_ds": monitoring_ds,
        "log_ds": log_ds,
        "monitoring_alias": monitoring_alias,
        "monitoring_alias_created": monitoring_alias_created,
        "log_alias": log_alias,
        "log_alias_created": log_alias_created,
        "doc_count": monitoring_doc_count,
    }


def deploy_main_workflows(
    creds: dict[str, str],
    hdr: tuple[str, str],
    namespace: str,
    ds_info: dict,
    installed: dict,
    slack_webhook: str,
) -> None:
    """Deploy both workflow variants for the main agent, with Slack connector embedded."""
    kb_url = creds["KB_URL"]
    deployed_agent_id = installed.get("agent_id", _ins.AGENT_ID)
    deployed_wfs: list[str] = []

    _ins.step(7, "Workflow Setup")

    # Provision Slack connector first so the ID is embedded in the workflow YAML
    slack_connector_id = ""
    if slack_webhook:
        _ins.info("Provisioning Slack connector…")
        try:
            slack_connector_id = _ins.provision_connector(kb_url, hdr, namespace, slack_webhook)
            installed["connector_id"] = slack_connector_id
            _ins.ok(f"Slack connector ready: {slack_connector_id}")
        except RuntimeError as exc:
            _ins.warn(f"Slack connector failed: {exc}")
    else:
        _ins.info("No SLACK_WEBHOOK in values file — workflows will deploy without Slack step")

    def render(template_path: Path) -> str:
        yaml = template_path.read_text()
        yaml = yaml.replace("__METRICS_PATTERN__", ds_info["monitoring_ds"])
        yaml = yaml.replace("__AGENT_ID__", deployed_agent_id)
        if slack_connector_id:
            yaml = yaml.replace("__SLACK_CONNECTOR_ID__", slack_connector_id)
        else:
            yaml = strip_slack_from_yaml(yaml)
        return yaml

    # Alert workflow
    alert_id = "es-triage-alert"
    _ins.info(f"Deploying alert workflow: {alert_id}")
    try:
        _ins.deploy_workflow_yaml(
            kb_url, hdr, namespace, alert_id,
            render(_ins.WORKFLOW_ALERT_TEMPLATE),
            wf_name="ES Cluster Triage Summary",
        )
        deployed_wfs.append(alert_id)
        _ins.ok(f"Alert workflow deployed: {alert_id}")
    except _ins.WorkflowPermissionError:
        _ins.warn(f"Alert workflow skipped — API key lacks workflow privileges")
        _ins.info("Re-run install using the elastic superuser (username + password) to deploy workflows")
    except RuntimeError as exc:
        _ins.warn(f"Alert workflow failed: {exc}")

    # Scheduled workflow (1h)
    scheduled_id = "es-triage-scheduled"
    interval = "1h"
    _ins.info(f"Deploying scheduled workflow: {scheduled_id} (every {interval})")
    try:
        yaml_text = render(_ins.WORKFLOW_SCHEDULED_TEMPLATE).replace("__SCHEDULE_INTERVAL__", interval)
        _ins.deploy_workflow_yaml(
            kb_url, hdr, namespace, scheduled_id, yaml_text,
            wf_name="ES Cluster Triage Scheduled",
        )
        deployed_wfs.append(scheduled_id)
        _ins.ok(f"Scheduled workflow deployed: {scheduled_id}")
    except _ins.WorkflowPermissionError:
        _ins.warn(f"Scheduled workflow skipped — API key lacks workflow privileges")
    except RuntimeError as exc:
        _ins.warn(f"Scheduled workflow failed: {exc}")

    if deployed_wfs:
        installed["workflows"] = deployed_wfs

    _ins.INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
    _ins.INSTALLED_FILE.chmod(0o600)


def deploy_optional_bundle_silent(
    creds: dict[str, str],
    hdr: tuple[str, str],
    namespace: str,
    ds_info: dict,
    installed: dict,
    slack_connector_id: str = "",
) -> None:
    """Deploy app-index-triage bundle + both workflow variants without interactive prompts."""
    kb_url = creds["KB_URL"]
    bundle_dir = ROOT / "kibana-agent-builder" / "app-index-triage"
    if not bundle_dir.exists():
        _ins.info("Optional bundle not found — skipping")
        return

    _ins.step(10, "Optional Agent — Application Index Triage")

    # Load + patch tools
    manifest = _ins.load_json(bundle_dir / "manifest.json")
    agent_data = _ins.load_json(bundle_dir / manifest["agent"])
    skills = [_ins.load_json(bundle_dir / p) for p in manifest.get("skill_files", [])]
    tools = [_ins.load_json(bundle_dir / p) for p in manifest.get("tool_files", [])]
    deployed_agent_id = agent_data.get("id", "app-index-triage-agent")

    monitoring_target = ds_info.get("monitoring_alias", ".monitoring-es-*") if ds_info.get("monitoring_alias_created") else ds_info["monitoring_ds"]
    log_target = ds_info.get("log_alias", "elastic-cloud-logs-8")

    for tool in tools:
        cfg = tool.get("configuration", {})
        query = cfg.get("query", "")
        query = re.sub(r"FROM \.monitoring-es-\S+", f"FROM {monitoring_target}", query)
        if log_target != "elastic-cloud-logs-8":
            query = query.replace("FROM elastic-cloud-logs-8", f"FROM {log_target}")
        cfg["query"] = query
        tool["configuration"] = cfg

    _ins.ok(f"Tool queries patched: monitoring → {monitoring_target}, logs → {log_target}")

    # Remove previous optional installation
    _ins.info("Removing previous optional installation if any…")
    _ins.delete_if_exists(kb_url, hdr, _ins.space_path(namespace, f"/api/agent_builder/agents/{deployed_agent_id}"))
    for sk in skills:
        _ins.delete_if_exists(kb_url, hdr, _ins.space_path(namespace, f"/api/agent_builder/skills/{sk['id']}"))
    for t in tools:
        _ins.delete_if_exists(kb_url, hdr, _ins.space_path(namespace, f"/api/agent_builder/tools/{t['id']}"))

    total = len(tools) + len(skills) + 1
    done_count = 0

    def tick(label: str) -> None:
        nonlocal done_count
        done_count += 1
        _ins.progress_bar(label, done_count, total)

    for tool in tools:
        try:
            _ins.kibana_request(kb_url, hdr, "POST", _ins.space_path(namespace, "/api/agent_builder/tools"), body=tool)
            installed.setdefault("optional_tools", []).append(tool["id"])
        except RuntimeError as exc:
            if "HTTP 409" in str(exc):
                installed.setdefault("optional_tools", []).append(tool["id"])
            else:
                raise
        tick(f"Tool: {tool['id'][:44]}")

    for skill in skills:
        try:
            _ins.kibana_request(kb_url, hdr, "POST", _ins.space_path(namespace, "/api/agent_builder/skills"), body=skill)
            installed.setdefault("optional_skills", []).append(skill["id"])
        except RuntimeError as exc:
            if "HTTP 409" in str(exc):
                installed.setdefault("optional_skills", []).append(skill["id"])
            else:
                raise
        tick(f"Skill: {skill['id'][:44]}")

    try:
        _ins.kibana_request(kb_url, hdr, "POST", _ins.space_path(namespace, "/api/agent_builder/agents"), body=agent_data)
        installed.setdefault("optional_agents", []).append(deployed_agent_id)
    except RuntimeError as exc:
        if "HTTP 409" in str(exc):
            installed.setdefault("optional_agents", []).append(deployed_agent_id)
        else:
            raise
    tick(f"Agent: {deployed_agent_id}")
    _ins.ok(f"Optional agent '{deployed_agent_id}' deployed")

    _ins.INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
    _ins.INSTALLED_FILE.chmod(0o600)

    # Deploy both workflow variants for optional agent
    wf_suffix = "app-index-triage"
    alert_tmpl = ROOT / "workflows" / f"{wf_suffix}.workflow.yaml"
    scheduled_tmpl = ROOT / "workflows" / f"{wf_suffix}-scheduled.workflow.yaml"

    def render_opt(template_path: Path) -> str:
        yaml = template_path.read_text()
        yaml = yaml.replace("__METRICS_PATTERN__", ds_info["monitoring_ds"])
        yaml = yaml.replace("__AGENT_ID__", deployed_agent_id)
        if slack_connector_id:
            yaml = yaml.replace("__SLACK_CONNECTOR_ID__", slack_connector_id)
        else:
            yaml = strip_slack_from_yaml(yaml)
        return yaml

    sp_prefix = f"{namespace}-" if namespace != "default" else ""
    opt_deployed_wfs: list[str] = []

    if alert_tmpl.exists():
        alert_id = f"{sp_prefix}{wf_suffix}-alert"
        _ins.info(f"Deploying optional alert workflow: {alert_id}")
        try:
            _ins.deploy_workflow_yaml(
                kb_url, hdr, namespace, alert_id, render_opt(alert_tmpl),
                wf_name=f"App Index Triage Alert",
            )
            opt_deployed_wfs.append(alert_id)
            _ins.ok(f"Optional alert workflow deployed: {alert_id}")
        except _ins.WorkflowPermissionError:
            _ins.warn("Optional alert workflow skipped — API key lacks workflow privileges")
        except RuntimeError as exc:
            _ins.warn(f"Optional alert workflow failed: {exc}")

    if scheduled_tmpl.exists():
        interval = "1h"
        scheduled_id = f"{sp_prefix}{wf_suffix}-scheduled"
        _ins.info(f"Deploying optional scheduled workflow: {scheduled_id} (every {interval})")
        try:
            yaml_text = render_opt(scheduled_tmpl).replace("__SCHEDULE_INTERVAL__", interval)
            _ins.deploy_workflow_yaml(
                kb_url, hdr, namespace, scheduled_id, yaml_text,
                wf_name=f"App Index Triage Scheduled",
            )
            opt_deployed_wfs.append(scheduled_id)
            _ins.ok(f"Optional scheduled workflow deployed: {scheduled_id}")
        except _ins.WorkflowPermissionError:
            _ins.warn("Optional scheduled workflow skipped — API key lacks workflow privileges")
        except RuntimeError as exc:
            _ins.warn(f"Optional scheduled workflow failed: {exc}")

    if opt_deployed_wfs:
        installed.setdefault("optional_workflows", []).extend(opt_deployed_wfs)
        _ins.INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
        _ins.INSTALLED_FILE.chmod(0o600)


def main() -> int:
    values_file = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_VALUES_FILE
    if not values_file.exists():
        print(f"ERROR: values file not found: {values_file}")
        return 1

    vals = load_values(values_file)

    kb_url = vals.get("KB_URL", "").rstrip("/")
    if not kb_url:
        print("ERROR: KB_URL not set in values file")
        return 1

    es_url = vals.get("ES_URL", "").rstrip("/")
    if not es_url:
        try:
            es_url = derive_es_url(kb_url)
            print(f"  Derived ES_URL: {es_url}")
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1

    api_key = vals.get("ES_API_KEY", "")
    slack_webhook = vals.get("SLACK_WEBHOOK", "")

    if not api_key:
        print("ERROR: ES_API_KEY not set in values file")
        return 1

    creds: dict[str, str] = {
        "KB_URL": kb_url,
        "ES_URL": es_url,
        "AUTH_TYPE": "apikey",
        "ES_API_KEY": api_key,
    }
    hdr = _ins.build_auth_header(creds)

    # NAMESPACE: values.txt > CLI arg (--namespace=X) > default
    namespace = vals.get("NAMESPACE", "default")
    for arg in sys.argv[1:]:
        if arg.startswith("--namespace="):
            namespace = arg.split("=", 1)[1]
        elif arg not in (str(DEFAULT_VALUES_FILE),) and not arg.startswith("--") and not arg.endswith(".txt"):
            namespace = arg  # positional second arg treated as namespace
    print(f"  Namespace: {namespace}")

    _ins.open_log()
    _ins.print_banner()

    try:
        # Step 3: validate auth
        _ins.validate_auth(creds, hdr)

        # Step 5: data sources (non-interactive)
        ds_info = build_ds_info(creds)

        # Step 6: deploy main bundle
        installed = _ins.deploy_all(creds, hdr, namespace, ds_info)

        # Step 7: workflows + Slack
        deploy_main_workflows(creds, hdr, namespace, ds_info, installed, slack_webhook)

        # Step 8: verify
        _ins.verify_deployment(creds, hdr, namespace)

        # Step 10: optional agent (reuse same Slack connector if provisioned)
        opt_connector_id = installed.get("connector_id", "")
        deploy_optional_bundle_silent(creds, hdr, namespace, ds_info, installed, slack_connector_id=opt_connector_id)
        installed["optional_namespace"] = namespace
        _ins.INSTALLED_FILE.write_text(json.dumps(installed, indent=2))
        _ins.INSTALLED_FILE.chmod(0o600)

        # Final summary
        _ins.print_summary(creds, namespace, ds_info)

    except SystemExit as exc:
        return int(exc.code or 1)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        return 1
    except Exception as exc:
        print(f"\n  FATAL: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        _ins.close_log()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
