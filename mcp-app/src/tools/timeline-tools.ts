import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const CLUSTER_HEALTH_URI = "ui://cluster-health/mcp-app.html";
const INDEX_LIFECYCLE_URI = "ui://index-lifecycle/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter.");

interface ClusterTimelineRow {
  bucket: string;
  "elasticsearch.cluster.name": string;
  "elasticsearch.cluster.stats.status": string;
  avg_heap_pct: number | null;
  avg_shards: number | null;
  avg_nodes: number | null;
}

interface NodeTimelineRow {
  bucket: string;
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  avg_heap_pct: number | null;
  avg_cpu_pct: number | null;
}

interface IndexTimelineRow {
  bucket: string;
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  total_indexing: number | null;
  total_search: number | null;
}

export function registerTimelineTools(server: McpServer): void {

  // ── cluster_timeline ────────────────────────────────────────────────────
  registerAppTool(
    server,
    "cluster_timeline",
    {
      title: "Cluster Timeline",
      description: "Show a time-bucketed trend of cluster heap%, status, and shard counts. Returns data for area charts and status heatmaps.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: CLUSTER_HEALTH_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<ClusterTimelineRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "cluster_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.cluster.stats.status IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS avg_heap_pct = AVG(elasticsearch.cluster.stats.nodes.jvm.memory.heap.used.pct), avg_shards = AVG(elasticsearch.cluster.stats.indices.shards.count), avg_nodes = AVG(elasticsearch.cluster.stats.nodes.count), elasticsearch.cluster.stats.status = LAST(elasticsearch.cluster.stats.status) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name
| SORT bucket ASC
| LIMIT 500`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      const byCluster: Record<string, { ts: string; heap_pct: number; status: string; shards: number; nodes: number }[]> = {};
      for (const r of rows) {
        const cn = r["elasticsearch.cluster.name"];
        (byCluster[cn] ??= []).push({
          ts: r.bucket,
          heap_pct: r.avg_heap_pct != null ? Math.round(r.avg_heap_pct * 100) : 0,
          status: r["elasticsearch.cluster.stats.status"] ?? "unknown",
          shards: r.avg_shards != null ? Math.round(r.avg_shards) : 0,
          nodes: r.avg_nodes != null ? Math.round(r.avg_nodes) : 0,
        });
      }

      const clusters = Object.entries(byCluster).map(([cluster_name, points]) => ({
        cluster_name,
        points,
      }));

      const payload = { tool: "cluster_timeline", lookback, clusters };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── node_metrics_timeline ───────────────────────────────────────────────
  registerAppTool(
    server,
    "node_metrics_timeline",
    {
      title: "Node Metrics Timeline",
      description: "Show per-node heap% and CPU% trends over time. Returns multi-series data for area charts and sparklines.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: CLUSTER_HEALTH_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<NodeTimelineRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS avg_heap_pct = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct), avg_cpu_pct = AVG(elasticsearch.node.stats.process.cpu.pct) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.node.name
| SORT bucket ASC
| LIMIT 1000`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      // Group by cluster → node → time series
      const byClusterNode: Record<string, Record<string, { ts: string; heap_pct: number; cpu_pct: number }[]>> = {};
      for (const r of rows) {
        const cn = r["elasticsearch.cluster.name"];
        const nn = r["elasticsearch.node.name"];
        (byClusterNode[cn] ??= {})[nn] ??= [];
        byClusterNode[cn][nn].push({
          ts: r.bucket,
          heap_pct: r.avg_heap_pct != null ? Math.round(r.avg_heap_pct * 100) : 0,
          cpu_pct: r.avg_cpu_pct != null ? Math.round(r.avg_cpu_pct * 100) : 0,
        });
      }

      const clusters = Object.entries(byClusterNode).map(([cluster_name, nodeMap]) => ({
        cluster_name,
        nodes: Object.entries(nodeMap).map(([node_name, points]) => ({ node_name, points })),
      }));

      const payload = { tool: "node_metrics_timeline", lookback, clusters };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── index_metrics_timeline ──────────────────────────────────────────────
  registerAppTool(
    server,
    "index_metrics_timeline",
    {
      title: "Index Metrics Timeline",
      description: "Show per-index indexing and search operation rates over time. Returns time-series data for trend charts.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: INDEX_LIFECYCLE_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<IndexTimelineRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE NOT STARTS_WITH(elasticsearch.index.name, ".")
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS total_indexing = MAX(elasticsearch.index.stats.total.indexing.index.count), total_search = MAX(elasticsearch.index.stats.total.search.query.count) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name
| SORT bucket ASC
| LIMIT 2000`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      // Group by cluster → index → time series; keep top 10 most active
      const byIndex: Record<string, { cluster_name: string; points: { ts: string; indexing: number; search: number }[]; total_ops: number }> = {};
      for (const r of rows) {
        const key = `${r["elasticsearch.cluster.name"]}/${r["elasticsearch.index.name"]}`;
        const cn = r["elasticsearch.cluster.name"];
        if (!byIndex[key]) {
          byIndex[key] = { cluster_name: cn, points: [], total_ops: 0 };
        }
        const ops = (r.total_indexing ?? 0) + (r.total_search ?? 0);
        byIndex[key].total_ops += ops;
        byIndex[key].points.push({
          ts: r.bucket,
          indexing: r.total_indexing ?? 0,
          search: r.total_search ?? 0,
        });
      }

      const indices = Object.entries(byIndex)
        .sort((a, b) => b[1].total_ops - a[1].total_ops)
        .slice(0, 15)
        .map(([key, v]) => ({
          index_name: key.split("/").slice(1).join("/"),
          cluster_name: v.cluster_name,
          points: v.points,
        }));

      const payload = { tool: "index_metrics_timeline", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );
}
