import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://logs-security/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const nodeName = z.string().optional().default("").describe("Optional node name filter; use an empty string to query all nodes.");

interface ErrorLogRow {
  bucket: string;
  "log.level": string;
  "host.name": string | null;
  "error.message": string | null;
  events: number;
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
      description: "Summarize WARN and ERROR logs by node and message. Groups log events into 15-minute buckets for pattern identification.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<ErrorLogRow>(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE log.level IN ("ERROR", "WARN")
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR host.name == ?node_name)
| STATS events = COUNT() BY bucket = DATE_TRUNC(15 minutes, @timestamp), log.level, host.name, error.message
| SORT bucket DESC, events DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const logs = rows.map((r) => ({
        bucket: r.bucket,
        level: r["log.level"],
        host: r["host.name"],
        message: r["error.message"],
        events: r.events,
      }));

      // Aggregate: top error messages, level breakdown
      const errorCount = rows.filter((r) => r["log.level"] === "ERROR").reduce((s, r) => s + r.events, 0);
      const warnCount = rows.filter((r) => r["log.level"] === "WARN").reduce((s, r) => s + r.events, 0);

      const payload = { tool: "error_log_summary", lookback, logs, summary: { error_count: errorCount, warn_count: warnCount } };
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
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
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
