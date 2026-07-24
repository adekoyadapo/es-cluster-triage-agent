import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://resource-pressure/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const nodeName = z.string().optional().default("").describe("Optional node name filter; use an empty string to query all nodes.");

interface DiskShardRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.cluster.stats.status": string;
  min_available_bytes: number | null;
  max_total_bytes: number | null;
  node_count: number | null;
  data_nodes: number | null;
  shards_count: number | null;
  primaries_count: number | null;
  used_pct: number | null;
}

interface ThreadPoolRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  write_rejected: number | null;
  search_rejected: number | null;
  bulk_rejected: number | null;
  write_queue: number | null;
  search_queue: number | null;
  bulk_queue: number | null;
  rejection_score: number | null;
}

interface FailureRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  write_rejected: number | null;
  bulk_rejected: number | null;
  coordinating_rejections: number | null;
  primary_rejections: number | null;
  replica_rejections: number | null;
  indexing_pressure_bytes: number | null;
  failure_score: number | null;
}

export function registerResourcePressureTools(server: McpServer): void {

  // ── disk_shard_pressure ────────────────────────────────────────────────
  registerAppTool(
    server,
    "disk_shard_pressure",
    {
      title: "Disk & Shard Pressure",
      description: "Detect clusters with low disk space, growing shard counts, or unhealthy cluster status. Shows disk usage and shard distribution per cluster.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<DiskShardRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "cluster_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.cluster.stats.status IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS min_available_bytes = MIN(elasticsearch.cluster.stats.nodes.fs.available.bytes), max_total_bytes = MAX(elasticsearch.cluster.stats.nodes.fs.total.bytes), node_count = MAX(elasticsearch.cluster.stats.nodes.count), data_nodes = MAX(elasticsearch.cluster.stats.nodes.data), shards_count = MAX(elasticsearch.cluster.stats.indices.shards.count), primaries_count = MAX(elasticsearch.cluster.stats.indices.shards.primaries) BY elasticsearch.cluster.name, elasticsearch.cluster.stats.status
| EVAL used_pct = CASE(max_total_bytes > 0, 1.0 - TO_DOUBLE(min_available_bytes) / max_total_bytes, NULL)
| SORT used_pct DESC, shards_count DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      const clusters = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        status: r["elasticsearch.cluster.stats.status"],
        disk_used_pct: r.used_pct != null ? Math.round(r.used_pct * 100) : null,
        disk_avail_gb: r.min_available_bytes != null ? +(r.min_available_bytes / 1e9).toFixed(2) : null,
        disk_total_gb: r.max_total_bytes != null ? +(r.max_total_bytes / 1e9).toFixed(2) : null,
        node_count: r.node_count,
        data_nodes: r.data_nodes,
        shards: r.shards_count,
        primaries: r.primaries_count,
      }));

      const payload = { tool: "disk_shard_pressure", lookback, clusters };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── thread_pool_rejections ─────────────────────────────────────────────
  registerAppTool(
    server,
    "thread_pool_rejections",
    {
      title: "Thread Pool Rejections",
      description: "Detect write, search, or bulk rejection pressure by node. Shows rejection counts and queue depths per thread pool.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<ThreadPoolRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR elasticsearch.node.name == ?node_name)
| STATS write_rejected = MAX(elasticsearch.node.stats.thread_pool.write.rejected.count), search_rejected = MAX(elasticsearch.node.stats.thread_pool.search.rejected.count), bulk_rejected = MAX(elasticsearch.node.stats.thread_pool.bulk.rejected.count), write_queue = MAX(elasticsearch.node.stats.thread_pool.write.queue.count), search_queue = MAX(elasticsearch.node.stats.thread_pool.search.queue.count), bulk_queue = MAX(elasticsearch.node.stats.thread_pool.bulk.queue.count), system_write_rejected = MAX(elasticsearch.node.stats.thread_pool.system_write.rejected.count), system_read_rejected = MAX(elasticsearch.node.stats.thread_pool.system_read.rejected.count) BY elasticsearch.cluster.name, elasticsearch.node.name
| EVAL rejection_score = COALESCE(write_rejected, 0) + COALESCE(search_rejected, 0) + COALESCE(bulk_rejected, 0) + COALESCE(system_write_rejected, 0) + COALESCE(system_read_rejected, 0)
| SORT rejection_score DESC, write_queue DESC, search_queue DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const nodes = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        node_name: r["elasticsearch.node.name"],
        write_rejected: r.write_rejected ?? 0,
        search_rejected: r.search_rejected ?? 0,
        bulk_rejected: r.bulk_rejected ?? 0,
        write_queue: r.write_queue ?? 0,
        search_queue: r.search_queue ?? 0,
        bulk_queue: r.bulk_queue ?? 0,
        rejection_score: r.rejection_score ?? 0,
      }));

      const payload = { tool: "thread_pool_rejections", lookback, nodes };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── indexing_failure_analysis ──────────────────────────────────────────
  registerAppTool(
    server,
    "indexing_failure_analysis",
    {
      title: "Indexing Failure Analysis",
      description: "Spot write and ingest failure proxies that can block indexing progress. Shows coordinating, primary, and replica rejections per node.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<FailureRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR elasticsearch.node.name == ?node_name)
| STATS write_rejected = MAX(elasticsearch.node.stats.thread_pool.write.rejected.count), bulk_rejected = MAX(elasticsearch.node.stats.thread_pool.bulk.rejected.count), indexing_pressure_bytes = MAX(elasticsearch.node.stats.indexing_pressure.memory.current.all.bytes), coordinating_rejections = MAX(elasticsearch.node.stats.indexing_pressure.memory.total.coordinating.rejections), primary_rejections = MAX(elasticsearch.node.stats.indexing_pressure.memory.total.primary.rejections), replica_rejections = MAX(elasticsearch.node.stats.indexing_pressure.memory.total.replica.rejections) BY elasticsearch.cluster.name, elasticsearch.node.name
| EVAL failure_score = COALESCE(write_rejected, 0) * 1000 + COALESCE(bulk_rejected, 0) * 1000 + COALESCE(coordinating_rejections, 0) * 1000 + COALESCE(primary_rejections, 0) * 1000 + COALESCE(replica_rejections, 0) * 1000 + COALESCE(indexing_pressure_bytes, 0) / 1000000
| SORT failure_score DESC, write_rejected DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const nodes = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        node_name: r["elasticsearch.node.name"],
        write_rejected: r.write_rejected ?? 0,
        bulk_rejected: r.bulk_rejected ?? 0,
        coordinating_rejections: r.coordinating_rejections ?? 0,
        primary_rejections: r.primary_rejections ?? 0,
        replica_rejections: r.replica_rejections ?? 0,
        indexing_pressure_mb: r.indexing_pressure_bytes != null ? +(r.indexing_pressure_bytes / 1e6).toFixed(2) : null,
        failure_score: r.failure_score != null ? Math.round(r.failure_score) : 0,
      }));

      const payload = { tool: "indexing_failure_analysis", lookback, nodes };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── registerAppResource: serves the compiled HTML view ─────────────────
  registerAppResource(
    server,
    "resource-pressure-view",
    RESOURCE_URI,
    { mimeType: RESOURCE_MIME_TYPE },
    async () => {
      const viewPath = resolveViewPath("resource-pressure");
      const html = fs.readFileSync(viewPath, "utf-8");
      return {
        contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }],
      };
    }
  );
}
