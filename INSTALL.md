# Installation Guide — ES Cluster Triage Agent

Step-by-step instructions for deploying the Elasticsearch Cluster Triage Agent into Kibana.

---

## Prerequisites

Before running the installer, ensure all of the following are in place on your **monitoring cluster**:

| Requirement | Detail |
|---|---|
| **Enterprise license** | Agent Builder, Workflows, AI Connector, and audit logging all require Enterprise (or an active trial) |
| **Stack Monitoring running** | Monitored clusters must be shipping metrics and logs to the monitoring cluster — verify with `GET .monitoring-es-*/_count` |
| **LLM / AI Connector** | An AI Connector (OpenAI, Anthropic, Bedrock, or Gemini) configured in Kibana → Stack Management → Connectors |
| **Python 3.8+** | `python3 --version` — no pip packages needed |
| **Network access** | The machine running the installer must reach both `KB_URL` (Kibana) and `ES_URL` (Elasticsearch) over HTTPS |

> See [docs/guide.html](docs/guide.html) → *Critical Setup Guide* for step-by-step instructions on each prerequisite.

---

## 1. Clone the repository

```bash
git clone https://github.com/adekoyadapo/es-cluster-triage-agent.git
cd es-cluster-triage-agent
```

---

## 2. Set up credentials

The installer collects all credentials interactively — no `.env` file is required to run it.
However, copying `.env.example` → `.env` provides a convenient reference and is required if you
use the optional MCP desktop app.

```bash
cp .env.example .env
# Edit .env and fill in your values
```

### Option A — API Key (recommended)

An API key gives you fine-grained control over exactly what the installer and agent can access.

**Step 1 — Generate the key in Kibana Dev Tools:**

```json
POST /_security/api_key
{
  "name": "es-cluster-triage-installer",
  "role_descriptors": {
    "triage-installer": {
      "cluster": ["monitor", "monitor_inference"],
      "indices": [{
        "names": ["<your-monitoring-pattern>", "<your-log-pattern>"],
        "privileges": ["manage", "read", "view_index_metadata"],
        "allow_restricted_indices": true
      }],
      "applications": [{
        "application": "kibana-.kibana",
        "privileges": ["all"],
        "resources": ["*"]
      }]
    }
  }
}
```

Replace `<your-monitoring-pattern>` and `<your-log-pattern>` with the patterns you'll enter
at step 5 of the installer (e.g. `.monitoring-es-*` and `filebeat-*`).

The response contains an `encoded` field — paste that value into `API_KEY` in your `.env`.

> The installer also displays this exact snippet at startup — you do not need to memorise it.

**Step 2 — Fill in `.env`:**

```bash
KB_URL=https://your-cluster.kb.us-east-1.aws.found.io
ES_URL=https://your-cluster.es.us-east-1.aws.found.io
API_KEY=<paste encoded value here>
NAMESPACE=default         # or your target Kibana space
MONITORING_DS=.monitoring-es-*
LOG_DS=elastic-cloud-logs-8
```

**Why `"all"` on `kibana-.kibana`?**
The installer deploys across Agent Builder, Workflows, and Actions. Using `"privileges": ["all"]`
with `"resources": ["*"]` is the only reliable way to cover all features with a single API key —
narrower privilege names (e.g. `feature_agentBuilder.all`) do not reliably grant cross-feature access.

**Why `monitor_inference`?**
Required for the Elasticsearch Inference API used by agent chat and ES|QL generation.

**Why `allow_restricted_indices: true`?**
Required when the monitoring pattern includes system indices (`.monitoring-es-*`).
Omit this flag if you use `metrics-elasticsearch.*` or other non-system patterns.

---

### Option B — Username / Password

Using the `elastic` superuser (or any user with the `superuser` role) requires no privilege tuning.
The installer accepts username/password at the authentication prompt.

**Fill in `.env`:**

```bash
KB_URL=https://your-cluster.kb.us-east-1.aws.found.io
ES_URL=https://your-cluster.es.us-east-1.aws.found.io
# ES_USERNAME=elastic
# ES_PASSWORD=your-password
NAMESPACE=default
MONITORING_DS=.monitoring-es-*
LOG_DS=elastic-cloud-logs-8
```

> Note: the `ES_USERNAME` / `ES_PASSWORD` variables in `.env` are read by the MCP desktop app.
> The **installer itself** always prompts interactively — it does not read these from `.env`.

---

## 3. Run the installer

```bash
python3 install/install.py
```

To deploy or re-deploy the optional Application Index Triage Agent without re-running the full install, use the `--optional-only` flag. It loads saved credentials and datastream settings from the previous run:

```bash
python3 install/install.py --optional-only
# short form:
python3 install/install.py -o
```

The installer walks through 10 interactive steps:

| Step | What happens |
|---|---|
| 1 · Prerequisite check | Verifies Python version and bundle files |
| 2 · Connection details | Prompts for `KB_URL`, `ES_URL`, auth method — credentials are never echoed |
| 3 · Auth validation | Live connectivity test against both endpoints |
| 4 · Kibana space | Choose or create the target Kibana space |
| 5 · Datastream config | Enter monitoring + log patterns; runs an ES\|QL smoke test |
| 6 · Deployment | Deploys tools → skills → agent (progress bar shown) |
| 7 · Workflow setup | Choose alert-triggered, scheduled, both, or agent-only |
| 8 · Verification | Confirms every component is reachable in Kibana |
| 9 · Live validation | Runs a live `cluster_health_summary` query |
| 10 · Optional agents | Offers to deploy the Application Index Triage Agent (see below) |

---

## Optional: Application Index Triage Agent

After the main agent is deployed and validated, the installer offers to deploy a second optional persona:

> **Application Index Triage Agent** — drills into a single application index to find ingestion failures, mapping issues, ILM stalls, slow operations, and audit events.

It reuses the same monitoring and log datastreams configured in step 5. No additional credentials or patterns are needed.

**Skills included:**

| Skill | Investigates |
|---|---|
| Index Health & Allocation | Unassigned shards, recovery stalls, shard-limit blocks |
| Mapping & Schema | `mapper_parsing_exception`, field-count explosion, dynamic mapping |
| Lifecycle & Rollover | ILM step errors, rollover failures, broken write aliases |
| Ingestion Performance & Slow Ops | Bulk rejections, indexing failures, slow-log latency, circuit breakers |
| Audit & Deprecations | `access_denied` spikes, admin actions (create/delete/put_mapping), deprecation warnings |

Built-in `observability.investigation` is also available for service-level questions tied to the index.

To deploy without re-running the full installer, answer **Yes** at step 10. The optional agent deploys into the same Kibana space as the main agent and can be uninstalled together via `python3 install/uninstall.py`.

When the installer finishes it prints the direct Kibana URL to your deployed agent.

---

## 4. Post-install verification

```bash
python3 install/verify.py
```

Re-checks all deployed components without re-running the full install. Useful after a Kibana upgrade
or if you suspect a partial deployment.

---

## 5. Uninstall

```bash
python3 install/uninstall.py
```

Reads `install/.installed.json` and removes only what the installer created — tools, skills, agent,
workflows, connector (if any), and the monitoring alias. The Kibana space and source files are not touched.

The uninstaller asks **separately** whether to remove the main cluster triage agent bundle and whether to remove the optional Application Index Triage Agent bundle. The optional prompt is only shown if the optional agent was installed.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `HTTP 403 on POST /api/workflows/workflow` | API key lacks workflow management privileges | Use `"all"` on `kibana-.kibana` with `"resources": ["*"]` — see Option A above |
| `HTTP 403 on alias creation` | API key lacks `manage` index privilege | The installer falls back gracefully — tools query raw patterns directly |
| `⚠ Kibana URL uses plain HTTP` | Non-HTTPS URL entered | Use `https://` — plain HTTP sends credentials in clear text |
| `API key not found` | Wrong encoded value pasted | Copy the `encoded` field from the `POST /_security/api_key` response, not `id` or `api_key` |
| `Set KIBANA_URL` fatal error | `KB_URL` not set in environment | The installer prompts for this interactively — the `.env` file is only needed for the MCP app |
| `Agent chat did not return a response` | No LLM connector configured on the agent | Open Agent Builder → your agent → Settings → Model and assign a connector |
| Optional agent not visible in Agent Builder | Not yet deployed | Run `python3 install/install.py --optional-only` to deploy it without re-running the full install |

---

## Reference

| Resource | Link |
|---|---|
| Full installation and setup guide | [docs/guide.html](docs/guide.html) |
| API reference and implementation notes | [install/IMPLEMENTATION.md](install/IMPLEMENTATION.md) |
| Environment variable template | [.env.example](.env.example) |
| MCP desktop app env template | [mcp-app/.env.example](mcp-app/.env.example) |
