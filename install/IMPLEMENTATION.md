# Implementation Reference — ES Cluster Triage Agent

Captured from the live `es-cluster-triage` Kibana space.

---

## Deployed Components

### Kibana Space
- **ID**: `es-cluster-triage`
- **Kibana**: `https://prj-observe-f6c5ada6.kb.us-east-1.aws.found.io:443`

### Agent
- **ID**: `es-cluster-triage-agent`
- **Name**: ES Cluster Triage Agent
- **Description**: AI-powered day-2 operations assistant for Elasticsearch. Triages cluster health, resource pressure, index issues, ILM problems, and security events using live ES|QL queries against your monitoring stream.
- **Skills**: 5 (see below)
- **Tools**: 21 (19 custom ES|QL + 2 platform tools)

### Skills (5)

| ID | Purpose |
|---|---|
| `es-cluster-triage-cluster-health-triage` | Cluster health, node loss, heap/CPU/GC |
| `es-cluster-triage-resource-pressure-triage` | Disk pressure, thread-pool rejections |
| `es-cluster-triage-index-pressure-triage` | Hot shards, allocation failures, slow queries |
| `es-cluster-triage-index-lifecycle-triage` | Growth, mapping explosion, ILM, hotspots |
| `es-cluster-triage-logs-security-triage` | Error logs, audit/security events |

### Custom ES|QL Tools (19)

All tools are namespaced `es-cluster-triage-{tool_name}` in the deployed space.

| Tool ID | Skill Group | Data Source |
|---|---|---|
| `es-cluster-triage-cluster_health_summary` | Cluster Health | `.monitoring-es-*` — `cluster_stats` metricset |
| `es-cluster-triage-red_yellow_periods` | Cluster Health | `.monitoring-es-*` — cluster status history |
| `es-cluster-triage-node_last_seen` | Cluster Health | `.monitoring-es-*` — last node check-in |
| `es-cluster-triage-node_pressure_summary` | Cluster Health | `.monitoring-es-*` — per-node CPU/heap/disk |
| `es-cluster-triage-jvm_gc_pressure` | Cluster Health | `.monitoring-es-*` — GC pauses and duration |
| `es-cluster-triage-disk_shard_pressure` | Resource Pressure | `.monitoring-es-*` — disk watermarks |
| `es-cluster-triage-thread_pool_rejections` | Resource Pressure | `.monitoring-es-*` — write/search/bulk queues |
| `es-cluster-triage-index_pressure_analysis` | Index Pressure | `.monitoring-es-*` — stressed indices |
| `es-cluster-triage-unassigned_shard_analysis` | Index Pressure | `.monitoring-es-*` — unassigned shards/reasons |
| `es-cluster-triage-indexing_failure_analysis` | Index Pressure | `.monitoring-es-*` — failed indexing operations |
| `es-cluster-triage-hot_shards_analysis` | Index Pressure | `.monitoring-es-*` — CPU/write hotspots by shard |
| `es-cluster-triage-slow_index_queries` | Index Pressure | `.monitoring-es-*` — slow query log |
| `es-cluster-triage-index_growth_analysis` | Index Lifecycle | `.monitoring-es-*` — doc/storage growth rate |
| `es-cluster-triage-mapping_explosion_detection` | Index Lifecycle | `.monitoring-es-*` — field count/cardinality |
| `es-cluster-triage-ilm_stuck_indices` | Index Lifecycle | `.monitoring-es-*` — ILM phase/action stuck |
| `es-cluster-triage-indexing_hotspots` | Index Lifecycle | `.monitoring-es-*` — concentrated indexing |
| `es-cluster-triage-search_hotspots` | Index Lifecycle | `.monitoring-es-*` — concentrated search |
| `es-cluster-triage-error_log_summary` | Logs & Security | `elastic-cloud-logs-8` — error/warning patterns |
| `es-cluster-triage-audit_security_events` | Logs & Security | `elastic-cloud-logs-8` — auth and access events |

**Important:** `error_log_summary` and `audit_security_events` query `elastic-cloud-logs-8` by default, not `.monitoring-es-*`. When installing with a custom log pattern (step 5 of installer), these tools are patched separately from the monitoring tools.

### Platform Tools (2 built-in, referenced in agent)
- `platform.core.list_indices`
- `platform.core.get_index_mapping`

### Workflows (2)

#### Alert-triggered Workflow
- **ID**: `es-cluster-triage-summary-alert` (or `{namespace}-es-cluster-triage-summary-alert` in non-default spaces)
- **Template**: `workflows/es-cluster-triage.workflow.yaml`
- **Trigger**: `type: alert` — fires when any Kibana rule triggers
- **Steps**:
  1. `metrics_summary` — ES|QL query against monitoring index
  2. `summarize` — AI agent (`es-cluster-triage-agent`) produces Markdown report

#### Scheduled Workflow
- **ID**: `es-cluster-triage-summary-scheduled` (or `{namespace}-es-cluster-triage-summary-scheduled`)
- **Template**: `workflows/es-cluster-triage-scheduled.workflow.yaml`
- **Trigger**: `type: scheduled` — runs at a user-specified interval (e.g. `1h`, `24h`)
- **Steps**:
  1. `metrics_summary` — ES|QL query against monitoring index
  2. `summarize` — AI agent produces Markdown report

#### Optional Slack Connector
- **Name**: `ES Cluster Triage Slack`
- **Type**: `.slack` (Webhook)
- **When**: Only provisioned if user opts in during install (step 7)
- **Note**: Not embedded in workflow YAML — provisioned as a connector the user can wire manually

---

## Datastream Configuration

### Monitoring Tools
Query: `.monitoring-es-*` (default — Stack Monitoring internal collection)

Other supported patterns:
- `.monitoring-es-8-mb` — Stack Monitoring v8 (internal)
- `metrics-elasticsearch.*` — Elastic Agent Metricbeat integration

### Log / Security Tools
Query: `elastic-cloud-logs-8` (default — Elastic Cloud logs)

Other supported patterns:
- `logs-elasticsearch.*` — Elastic Agent Filebeat integration
- `.logs-endpoint.*` — Endpoint security logs

### Custom Pattern Handling
The installer (step 5) prompts for both patterns separately and validates each:
1. Checks document count (`/{pattern}/_count`)
2. Runs an ES|QL smoke test
3. Creates a named alias `es-triage-monitoring` → monitoring pattern
4. Patches tool queries at deploy time:
   - Monitoring tools: `FROM .monitoring-es-*` → `FROM {your-pattern}`
   - Log/security tools: `FROM elastic-cloud-logs-8` → `FROM {your-log-pattern}`
5. Shows an API key coverage reminder listing the exact patterns the key must cover

---

## Agent Instructions Summary

The agent follows this routing logic:

```
Broad cluster issue            → es-cluster-triage-cluster-health-triage skill
Disk / rejection pressure      → es-cluster-triage-resource-pressure-triage skill
Single index / allocation      → es-cluster-triage-index-pressure-triage skill
Storage / growth / ILM issues  → es-cluster-triage-index-lifecycle-triage skill
Auth / log errors              → es-cluster-triage-logs-security-triage skill
```

Response format (always):
1. Summary
2. Most likely issue
3. Evidence (from tool results)
4. Impacted cluster/node/index
5. Time window
6. Recommended next checks
7. Confidence level

---

## Workflow YAML Structure

### Alert Workflow
```yaml
name: ES Cluster Triage Summary
triggers:
  - type: alert      # triggered by any Kibana rule alert
steps:
  - type: elasticsearch.request   # ES|QL query on monitoring stream
  - type: ai.agent                # agent-id: es-cluster-triage-agent
```

### Scheduled Workflow
```yaml
name: ES Cluster Triage Scheduled
triggers:
  - type: scheduled
    with:
      interval: "1h"             # configured at install time
steps:
  - type: elasticsearch.request   # ES|QL query on monitoring stream
  - type: ai.agent                # agent-id: es-cluster-triage-agent
```

Template placeholders replaced at deploy time:
- `__METRICS_PATTERN__` → monitoring datastream pattern
- `__AGENT_ID__` → `es-cluster-triage-agent`
- `__SCHEDULE_INTERVAL__` → user-specified interval (scheduled workflow only)

---

## API Endpoints Used

| Operation | Method | Path |
|---|---|---|
| Deploy tool | POST | `/s/{space}/api/agent_builder/tools` |
| Deploy skill | POST | `/s/{space}/api/agent_builder/skills` |
| Deploy agent | POST | `/s/{space}/api/agent_builder/agents` |
| Deploy workflow | POST | `/s/{space}/api/workflows/workflow` |
| Update workflow | PUT | `/s/{space}/api/workflows/workflow/{id}` |
| Deploy connector | POST | `/s/{space}/api/actions/connector/{id}` |
| Create space | POST | `/api/spaces/space` |
| Auth check | GET | `/api/status` |
| ES health | GET | `/_cluster/health` |
| Doc count | GET | `/{pattern}/_count` |
| ES|QL query | POST | `/_query` |
| Alias create | POST | `/_aliases` |

### Important: Workflow ID Scoping

Kibana workflow IDs are **globally unique across all spaces**. When deploying to a namespace
other than the default, prefix the workflow ID with the namespace:

```
{namespace}-es-cluster-triage-summary-alert
{namespace}-es-cluster-triage-summary-scheduled
```

This prevents 409 conflicts when the same workflow template is deployed into multiple spaces.

The installer handles this automatically. The `deploy_workflow_yaml()` function uses POST-then-PUT
(catches 409 and falls back to PUT) to handle idempotent re-deployments.

---

## Install Scripts

| Script | Purpose |
|---|---|
| `install/install.py` | Interactive 9-step installer — deploys all components |
| `install/uninstall.py` | Removes everything recorded in `.installed.json` |
| `install/verify.py` | Health-checks a live deployment against `.installed.json` |

### Local files created by install
- `install/.installed.json` — manifest of deployed IDs (chmod 600)
- `install/.credentials.local` — session metadata: URLs, auth type, datastream patterns (chmod 600, no secrets stored)
- `install/install.log` — append-only log of all API calls and results

All three files are in `.gitignore`.
