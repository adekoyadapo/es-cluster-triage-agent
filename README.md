# Elasticsearch Cluster Triage Agent

An AI-powered day-2 operations agent for Elasticsearch, deployed directly into [Kibana Agent Builder](https://www.elastic.co/docs/explore-analyze/ai-features/elastic-agent-builder). It investigates cluster health, resource pressure, index issues, ILM problems, and security events using 19 live ES|QL queries against your Stack Monitoring data — then produces a structured Markdown triage report.

---

## How it works

```
Alert fires (or schedule triggers)
        ↓
Kibana Workflow runs an ES|QL metrics query
        ↓
AI Agent interprets evidence using 19 diagnostic tools across 5 skill groups
        ↓
Structured Markdown report — Summary · Root cause · Evidence · Next checks
```

The agent is triggered either by a Kibana alerting rule or on a fixed schedule. It does not require manual intervention: once connected to a monitoring rule, triage runs automatically and results appear in the Kibana Workflow execution history.

---

## Prerequisites

| Requirement | Minimum | Notes | Docs |
|---|---|---|---|
| Kibana | 8.12+ | Agent Builder must be available | [Agent Builder](https://www.elastic.co/docs/explore-analyze/ai-features/elastic-agent-builder) |
| Elasticsearch | 8.x | Matching your Kibana version | |
| Stack Monitoring | Any | Sending data to `.monitoring-es-*` | [Stack Monitoring setup](https://www.elastic.co/docs/deploy-manage/monitor/stack-monitoring) |
| Python | 3.8+ | Stdlib only — no pip installs needed | |
| API credentials | — | API key or username/password with admin role | [Elasticsearch API Keys](https://www.elastic.co/docs/deploy-manage/api-keys/elasticsearch-api-keys) |
| Node.js | 22+ | Optional — only needed to build the MCP desktop app | |

### Required permissions

The credentials provided at install time need:

- **Kibana**: `kibana_admin` or `superuser` role (to deploy agent components via the Agent Builder API)
- **Elasticsearch**: `read` + `view_index_metadata` on your monitoring and log datastream patterns

The installer displays the exact Dev Tools commands to create minimal-scope API keys at the start of the setup flow.

---

## Quick start

```bash
# Clone the repository
git clone https://github.com/adekoyadapo/es-cluster-triage-agent.git
cd es-cluster-triage-agent

# Run the interactive installer (no dependencies required)
python3 install/install.py
```

The installer walks you through 10 steps:

1. **Prerequisite check** — verifies Python version and bundle files
2. **Connection details** — Kibana URL, Elasticsearch URL, authentication
3. **Auth validation** — live connectivity test before any deployment
4. **Kibana space** — choose or create the target space (default: `default`)
5. **Datastream configuration** — set your monitoring and log patterns, runs ES|QL smoke test
6. **Deployment** — tools → skills → agent → workflow, with a live progress bar
7. **Workflow setup** — choose alert-triggered, scheduled (with interval), both, or agent-only
8. **Verification** — confirms every deployed component is reachable
9. **Live validation** — runs a `cluster_health_summary` ES|QL query and streams a test question to the agent
10. **Optional agents** — offers to deploy the Application Index Triage Agent (use `--optional-only` / `-o` to re-deploy later without re-running the full install)

Credentials are **never** displayed in the terminal. A local install manifest is saved for clean uninstall.

---

## Datastream support

The agent ships with sensible defaults but works with any monitoring configuration:

| Data | Default pattern | Alternatives |
|---|---|---|
| Monitoring metrics | `.monitoring-es-*` | `.monitoring-es-8-mb`, `metrics-elasticsearch.*` |
| Error logs & audit | `elastic-cloud-logs-8` | `logs-elasticsearch.*`, `.logs-endpoint.*` |

At install time (step 5) the installer prompts for both patterns separately, validates them with a live doc count and ES|QL query, and patches the tool queries to use your actual patterns. It also shows which index patterns your API key must cover.

For more on Stack Monitoring data streams, see [Monitor Elasticsearch](https://www.elastic.co/docs/deploy-manage/monitor/stack-monitoring).

---

## Skill groups and tools

Five skill groups with 19 ES|QL tools cover the full triage surface:

### 🩺 Cluster Health Triage
*Use when: red/yellow cluster status, node loss, heap or CPU pressure, JVM GC issues*

| Tool | What it checks |
|---|---|
| `cluster_health_summary` | Overall status, shard count, heap and filesystem totals |
| `red_yellow_periods` | When and how long the cluster was in a degraded state |
| `node_last_seen` | Last check-in time per node — identifies dropped or restarted nodes |
| `node_pressure_summary` | Per-node CPU, heap, and disk breakdown |
| `jvm_gc_pressure` | GC pause rate and duration across nodes |

→ [ES|QL reference](https://www.elastic.co/docs/reference/query-languages/esql) · [Stack Monitoring data](https://www.elastic.co/docs/deploy-manage/monitor/stack-monitoring)

### 📊 Resource Pressure Triage
*Use when: disk pressure, shard imbalance, thread-pool rejections*

| Tool | What it checks |
|---|---|
| `disk_shard_pressure` | Disk watermarks and shard counts per node |
| `thread_pool_rejections` | Rejected operations by thread pool (write / search / bulk) |

→ [Thread pool settings](https://www.elastic.co/docs/reference/elasticsearch/configuration-reference/thread-pool-settings)

### 🔥 Index Pressure Triage
*Use when: one index is slow, shard allocation failing, write failures isolated to a workload*

| Tool | What it checks |
|---|---|
| `index_pressure_analysis` | Top stressed indices by shard pressure and write rate |
| `unassigned_shard_analysis` | Unassigned shards and their allocation reasons |
| `indexing_failure_analysis` | Rejected or failed indexing operations by index |
| `hot_shards_analysis` | Shards dominating CPU or write throughput |
| `slow_index_queries` | Slow-query log entries by index pattern |

→ [Diagnose unassigned shards](https://www.elastic.co/docs/troubleshoot/elasticsearch/diagnose-unassigned-shards)

### 📈 Index Lifecycle Triage
*Use when: storage runaway, mapping explosion, ILM stall, workload hotspots*

| Tool | What it checks |
|---|---|
| `index_growth_analysis` | Document and storage growth rate by index family |
| `mapping_explosion_detection` | Field count and cardinality growth warnings |
| `ilm_stuck_indices` | Indices stuck in a lifecycle phase with reason |
| `indexing_hotspots` | Which indices concentrate the indexing workload |
| `search_hotspots` | Which indices concentrate the search workload |

→ [Index Lifecycle Management](https://www.elastic.co/docs/manage-data/lifecycle/index-lifecycle-management)

### 🔍 Logs & Security Triage
*Use when: server errors or warnings, authentication failures, access denied, suspicious audit activity*

| Tool | What it checks |
|---|---|
| `error_log_summary` | Dominant error and warning patterns with counts and nodes |
| `audit_security_events` | Authentication, authorization, and access-denied events |

---

## Optional: Application Index Triage Agent

A second agent persona (`app-index-triage-agent`) can be deployed after the main agent. The installer asks at step 10 of the setup flow — answer **Yes** to deploy it into the same Kibana space.

It focuses on **per-index** problems on application data streams. It reuses the same monitoring and log datastreams configured for the main agent and requires no additional credentials or patterns.

### 5 skills, 16 tools (including 2 discovery tools for standard indices and data streams)

| Skill | Focus |
|---|---|
| Index Health & Allocation | Unassigned shards (NODE_LEFT, NO_VALID_SHARD_COPY, ALLOCATION_FAILED), recovery stalls |
| Mapping & Schema | `mapper_parsing_exception`, total-fields limit, fielddata memory growth, dynamic mapping abuse |
| Lifecycle & Rollover | ILM step errors, rollover failures, broken write aliases, DSL errors |
| Ingestion Performance & Slow Ops | Bulk rejections, server-log ERROR/WARN spikes, slow-log entries, circuit-breaker trips |
| Audit & Deprecations | `access_denied` spikes, admin actions (create/delete/put_mapping), deprecation warnings |

Two discovery tools help scope which index or data stream to investigate:

- `app-index-triage-active-log-indices` — finds standard indices with recent `elasticsearch.*` log events (log-based)
- `app-index-triage-active-ds-indices` — groups `.ds-*` backing indices back to their parent data stream name (monitoring-based)

Built-in `observability.investigation` is also assigned and triggered proactively on performance degradation (not only on errors).

**Data stream support:** backing indices are named `.ds-<name>-YYYY.MM.DD-NNNNNN`. Use a wildcard pattern — e.g. `*lab-activity-ds*` — to match all backing indices in monitoring and log queries. `platform.core.get_index_mapping` is NOT used; Kibana is connected to the monitoring cluster, not the monitored application cluster, so all analysis is log and monitoring based.

**Routing:** every tool requires an `index_pattern` parameter. The agent asks for it once if not provided in the prompt. Log events are filtered by `event.dataset` inside the user-configured log pattern, so it works with Filebeat, Elastic Agent, `elastic-cloud-logs-8`, or any other shipper that preserves ECS.

---

## Agent routing logic

The agent selects the appropriate skill group based on the incoming signal:

| Symptom | Entry skill | Escalates to |
|---|---|---|
| Red/yellow status, node loss, heap spike | Cluster Health | Resource Pressure if broad |
| Disk full, shard imbalance, rejections | Resource Pressure | Index Pressure if localized |
| Slow index, allocation failing, write errors | Index Pressure | Index Lifecycle if growth-related |
| Storage runaway, ILM stall, mapping explosion | Index Lifecycle | — |
| Auth errors, access denied, log spike | Logs & Security | Cluster Health if node-related |

### Report format

Every response follows this structure:

```
Summary          — one-sentence overall finding
Most likely issue — root cause hypothesis
Evidence         — key numbers or patterns from tool results
Impacted scope   — cluster / node / index name when identifiable
Time window      — the period the evidence covers
Next checks      — 2–3 recommended follow-up actions
Confidence       — High / Medium / Low with reason if low
```

---

## Automated workflows

Two workflow templates are included, both deployed by the installer:

### Alert-triggered workflow
Fires when any Kibana alerting rule triggers. The workflow receives alert context (`cluster_name`, `reason`, `node_name`), queries monitoring data for that cluster, and returns a triage report.

```yaml
triggers:
  - type: alert
steps:
  - type: elasticsearch.request   # ES|QL query on monitoring stream
  - type: ai.agent                # Returns structured Markdown report
```

To connect it to a Kibana rule:
1. Kibana → Observability → Alerts → Rules → create an **Elasticsearch cluster health** rule
2. Add the `ES Cluster Triage Summary` workflow as an action
3. Map `context.cluster_name` and `context.reason` to the workflow event variables

→ [Create and manage alerting rules](https://www.elastic.co/docs/explore-analyze/alerting/alerts/create-manage-rules)

### Scheduled workflow
Runs on a fixed interval (e.g. hourly) as a background health check — no alert required.

```yaml
triggers:
  - type: scheduled
    with:
      interval: "1h"
steps:
  - type: elasticsearch.request
  - type: ai.agent
```

The interval is set interactively during install. Results appear in the Kibana Workflow execution history.

→ [Kibana Workflows](https://www.elastic.co/docs/explore-analyze/workflows)

---

## Repository structure

```
es-cluster-triage-agent/
│
├── install/
│   ├── install.py              # Interactive 10-step installer (Python 3.8+, stdlib only)
│   ├── uninstall.py            # Removes only what install.py created
│   ├── verify.py               # Health-checks a live deployment
│   └── IMPLEMENTATION.md       # Full API reference and deployment notes
│
├── kibana-agent-builder/
│   ├── es-cluster-triage/
│   │   ├── manifest.json       # Bundle descriptor (tool_files, skill_files, agent path)
│   │   ├── agent.json          # Agent definition with instructions and skill routing
│   │   ├── tools/              # 19 ES|QL tool JSON definitions
│   │   └── skills/             # 5 skill group JSON definitions
│   └── app-index-triage/
│       ├── manifest.json       # Bundle descriptor for optional per-index triage agent
│       ├── agent.json          # Agent definition with per-index instructions and skill routing
│       ├── tools/              # 16 ES|QL tool JSON definitions (14 diagnostic + 2 discovery)
│       └── skills/             # 5 skill group JSON definitions
│
├── workflows/
│   ├── es-cluster-triage.workflow.yaml            # Alert-triggered workflow template
│   └── es-cluster-triage-scheduled.workflow.yaml  # Scheduled workflow template
│
├── agents/
│   └── elasticsearch_cluster_triage_agent.md      # Agent system prompt reference
│
├── mcp-app/                    # Optional MCP desktop app (Node.js 22+)
│   ├── manifest.json           # MCP app manifest
│   ├── main.ts                 # Entry point
│   ├── src/
│   │   ├── tools/              # Tool implementations per skill group
│   │   ├── views/              # React views for each diagnostic group
│   │   └── elastic/            # Elasticsearch client and ES|QL helpers
│   └── package.json
│
└── docs/
    ├── guide.html              # Full installation and reference guide
    └── index.html              # Slide presentation
```

---

## Management commands

```bash
# Deploy (interactive)
python3 install/install.py

# Re-deploy optional agent only (skips steps 1–9)
python3 install/install.py --optional-only

# Check a live deployment
python3 install/verify.py

# Remove everything the installer created
python3 install/uninstall.py
```

The uninstaller reads `install/.installed.json` and removes only the components it created — tools, skills, agent, workflows, connector (if any), and the ES alias. Source files and the Kibana space itself are not touched. The uninstaller asks **separately** whether to remove the main cluster triage agent bundle and whether to remove the optional Application Index Triage Agent bundle (the optional prompt is only shown if the optional agent was installed).

---

## Optional: MCP desktop app

The `mcp-app/` directory contains an MCP server that exposes all 19 tools with rich visual dashboards (gauges, charts, histograms) directly inside Claude Desktop.

```bash
cd mcp-app
npm install
npm run build
# Install the generated .mcpb file via Claude Desktop → Settings → Extensions
```

The installer offers to build this automatically at the end of the setup flow.

The app connects directly to Elasticsearch using credentials you configure in Claude Desktop. See `mcp-app/.env.example` for the required variables.

---

## Security

- Credentials are **never** echoed to the terminal during install — `getpass` is used throughout
- `install/.credentials.local` stores session metadata only (URLs, auth type, datastream names) — no secrets — with file mode `600`
- `install/.installed.json` and `install/install.log` are excluded from git via `.gitignore`
- `.env` and `.mcp.json` are excluded from git
- The installer log records only HTTP status codes and API paths, never credentials or key values
- API keys created for this agent should follow the least-privilege examples shown in the installer and in `docs/guide.html`

---

## Credentials and `.env`

The installer collects all credentials interactively — you do not need a `.env` file to run it.
A `.env` file is required only when using the **MCP desktop app** (`mcp-app/`), which reads connection
details from the environment at startup.

```bash
cp .env.example .env
# Edit .env and fill in your values
```

| Variable | Required | Description |
|---|---|---|
| `KB_URL` | Yes | Kibana URL — must be `https://` for remote clusters |
| `ES_URL` | Yes | Elasticsearch URL — must be `https://` for remote clusters |
| `API_KEY` | Option A | Encoded API key (`id:secret` or base64 value from Dev Tools response) |
| `ES_USERNAME` | Option B | Elasticsearch username (e.g. `elastic`) |
| `ES_PASSWORD` | Option B | Password for `ES_USERNAME` |
| `NAMESPACE` | No | Kibana space ID (default: `default`) |
| `MONITORING_DS` | No | Monitoring metrics pattern (default: `.monitoring-es-*`) |
| `LOG_DS` | No | Log and audit pattern (default: `elastic-cloud-logs-8`) |

Choose **Option A** (API key) or **Option B** (username/password) — not both.
For the required API key privileges and a minimal-scope key snippet, see [INSTALL.md](INSTALL.md#option-a--api-key-recommended).

> The MCP desktop app uses a separate set of variables (`ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`,
> `KIBANA_URL`, `KIBANA_API_KEY`) — see [mcp-app/.env.example](mcp-app/.env.example).

---

## Reference

| Resource | Link |
|---|---|
| Installation guide | [docs/guide.html](https://adekoyadapo.github.io/es-cluster-triage-agent/guide.html) |
| Full install walkthrough | [INSTALL.md](INSTALL.md) |
| Implementation reference | [install/IMPLEMENTATION.md](https://github.com/adekoyadapo/es-cluster-triage-agent/blob/main/install/IMPLEMENTATION.md) |
| Kibana Agent Builder | [elastic.co/docs](https://www.elastic.co/docs/explore-analyze/ai-features/elastic-agent-builder) |
| Stack Monitoring | [elastic.co/docs](https://www.elastic.co/docs/deploy-manage/monitor/stack-monitoring) |
| ES\|QL reference | [elastic.co/docs](https://www.elastic.co/docs/reference/query-languages/esql) |
| Kibana Workflows | [elastic.co/docs](https://www.elastic.co/docs/explore-analyze/workflows) |
| Kibana Alerting | [elastic.co/docs](https://www.elastic.co/docs/explore-analyze/alerting/alerts/create-manage-rules) |
| Index Lifecycle Management | [elastic.co/docs](https://www.elastic.co/docs/manage-data/lifecycle/index-lifecycle-management) |
| Diagnose unassigned shards | [elastic.co/docs](https://www.elastic.co/docs/troubleshoot/elasticsearch/diagnose-unassigned-shards) |
| Thread pool settings | [elastic.co/docs](https://www.elastic.co/docs/reference/elasticsearch/configuration-reference/thread-pool-settings) |
