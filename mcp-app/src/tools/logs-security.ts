import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://logs-security/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Cluster host-name prefix filter (e.g. 'es-prod'). Use an empty string to query all clusters.");
const nodeName = z.string().optional().default("").describe("Optional exact host.name to scope to a single node. Use an empty string to query all nodes.");

interface ErrorLogRow {
  "@timestamp": string;
  "log.level": string | null;
  "host.name": string | null;
  "message": string | null;
}

interface ClusterLogRow {
  "@timestamp": string;
  "log.level": string | null;
  "host.name": string | null;
  "message": string | null;
}

interface AuditRow {
  bucket: string;
  "event.action": string | null;
  "user.name": string | null;
  "source.ip": string | null;
  events: number;
}

export function registerLogsSecurityTools(server: McpServer): void {

  // ── error_log_summary ──────────────────────────────────────────────────
  registerAppTool(
    server,
    "error_log_summary",
    {
      title: "Error Log Summary",
      description: "Return recent WARN and ERROR server logs from elastic-cloud-logs-8. Scoped by cluster host-name prefix. Use after resource-pressure or health anomalies to find corroborating log evidence.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<ErrorLogRow>(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE log.level IN ("ERROR", "WARN")
| WHERE event.dataset == "elasticsearch.server"
| WHERE (?cluster_name == "" OR STARTS_WITH(host.name, ?cluster_name))
| WHERE (?node_name == "" OR host.name == ?node_name)
| KEEP @timestamp, log.level, host.name, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const logs = rows.map((r) => ({
        timestamp: r["@timestamp"],
        level: r["log.level"],
        host: r["host.name"],
        message: r["message"],
      }));

      const errorCount = logs.filter((r) => r.level === "ERROR").length;
      const warnCount = logs.filter((r) => r.level === "WARN").length;

      const payload = { tool: "error_log_summary", lookback, logs, summary: { error_count: errorCount, warn_count: warnCount } };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── cluster_log_events ─────────────────────────────────────────────────
  registerAppTool(
    server,
    "cluster_log_events",
    {
      title: "Cluster Log Events",
      description: "Query Elasticsearch server logs for cluster state transitions (health status changed), node join/leave, shard allocation events, OOM errors, and circuit-breaker trips. Call this after red_yellow_periods detects a non-green window to retrieve the verbatim root-cause reason.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<ClusterLogRow>(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (?cluster_name == "" OR STARTS_WITH(host.name, ?cluster_name))
| WHERE (?node_name == "" OR host.name == ?node_name)
| WHERE log.level IN ("ERROR", "WARN")
    OR message LIKE "*health status changed*"
    OR message LIKE "*node-left*"
    OR message LIKE "*node-join*"
    OR message LIKE "*allocation*"
    OR message LIKE "*OutOfMemory*"
    OR message LIKE "*circuit_break*"
    OR message LIKE "*recovered*"
| KEEP @timestamp, log.level, host.name, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const events = rows.map((r) => ({
        timestamp: r["@timestamp"],
        level: r["log.level"],
        host: r["host.name"],
        message: r["message"],
      }));

      const payload = { tool: "cluster_log_events", lookback, events };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── audit_security_events ──────────────────────────────────────────────
  registerAppTool(
    server,
    "audit_security_events",
    {
      title: "Audit Security Events",
      description: "Summarize authentication and authorization failures from audit or security logs. Shows user, source IP, and action patterns.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<AuditRow>(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.action IS NOT NULL
| WHERE event.outcome == "failure" OR event.action LIKE "*denied*" OR event.action LIKE "*authentication*"
| WHERE (?cluster_name == "" OR STARTS_WITH(host.name, ?cluster_name))
| WHERE (?node_name == "" OR host.name == ?node_name)
| STATS events = COUNT() BY bucket = DATE_TRUNC(15 minutes, @timestamp), event.action, user.name, source.ip
| SORT bucket DESC, events DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const events = rows.map((r) => ({
        bucket: r.bucket,
        action: r["event.action"],
        user: r["user.name"],
        source_ip: r["source.ip"],
        count: r.events,
      }));

      const totalFailures = rows.reduce((s, r) => s + r.events, 0);

      const payload = { tool: "audit_security_events", lookback, events, summary: { total_failures: totalFailures } };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── registerAppResource: serves the compiled HTML view ─────────────────
  registerAppResource(
    server,
    "logs-security-view",
    RESOURCE_URI,
    { mimeType: RESOURCE_MIME_TYPE },
    async () => {
      const viewPath = resolveViewPath("logs-security");
      const html = fs.readFileSync(viewPath, "utf-8");
      return {
        contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }],
      };
    }
  );
}
