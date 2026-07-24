import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://cluster-health/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const nodeName = z.string().optional().default("").describe("Optional node name filter; use an empty string to query all nodes.");

interface ClusterRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.cluster.stats.status": string;
  samples: number;
  last_seen: string;
  node_count: number | null;
  data_nodes: number | null;
  shards_count: number | null;
  primaries_count: number | null;
  heap_used_bytes: number | null;
  heap_max_bytes: number | null;
  fs_available_bytes: number | null;
  fs_total_bytes: number | null;
}

interface YellowRedRow {
  bucket: string;
  "elasticsearch.cluster.name": string;
  "elasticsearch.cluster.stats.status": string;
  samples: number;
  node_count: number | null;
  shards_count: number | null;
}

interface NodeLastSeenRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  last_seen: string;
  samples: number;
}

interface NodePressureRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  avg_heap_pct: number | null;
  max_heap_pct: number | null;
  avg_cpu_pct: number | null;
  max_cpu_pct: number | null;
  used_pct: number | null;
  pressure_score: number | null;
  write_rejected: number | null;
  search_rejected: number | null;
  segments_count: number | null;
}

interface JvmGcRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.node.name": string;
  avg_heap_pct: number | null;
  max_heap_pct: number | null;
  max_cpu_pct: number | null;
  young_gc_count: number | null;
  young_gc_ms: number | null;
  old_gc_count: number | null;
  old_gc_ms: number | null;
  gc_pressure_score: number | null;
}

export function registerClusterHealthTools(server: McpServer): void {

  // ── cluster_health_summary ─────────────────────────────────────────────
  registerAppTool(
    server,
    "cluster_health_summary",
    {
      title: "Cluster Health Summary",
      description: "Show cluster health status, shard pressure, heap and disk from Stack Monitoring. Returns a visual summary of all clusters.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<ClusterRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "cluster_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.cluster.stats.status IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS samples = COUNT(), last_seen = MAX(@timestamp), node_count = MAX(elasticsearch.cluster.stats.nodes.count), data_nodes = MAX(elasticsearch.cluster.stats.nodes.data), shards_count = MAX(elasticsearch.cluster.stats.indices.shards.count), primaries_count = MAX(elasticsearch.cluster.stats.indices.shards.primaries), heap_used_bytes = MAX(elasticsearch.cluster.stats.nodes.jvm.memory.heap.used.bytes), heap_max_bytes = MAX(elasticsearch.cluster.stats.nodes.jvm.memory.heap.max.bytes), fs_available_bytes = MAX(elasticsearch.cluster.stats.nodes.fs.available.bytes), fs_total_bytes = MAX(elasticsearch.cluster.stats.nodes.fs.total.bytes) BY elasticsearch.cluster.name, elasticsearch.cluster.stats.status
| SORT samples DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      const clusters = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        status: r["elasticsearch.cluster.stats.status"],
        samples: r.samples,
        last_seen: r.last_seen,
        node_count: r.node_count,
        data_nodes: r.data_nodes,
        shards: r.shards_count,
        primaries: r.primaries_count,
        heap_used_pct: r.heap_used_bytes != null && r.heap_max_bytes != null && r.heap_max_bytes > 0
          ? Math.round((r.heap_used_bytes / r.heap_max_bytes) * 100) : null,
        heap_used_gb: r.heap_used_bytes != null ? +(r.heap_used_bytes / 1e9).toFixed(2) : null,
        heap_max_gb: r.heap_max_bytes != null ? +(r.heap_max_bytes / 1e9).toFixed(2) : null,
        disk_avail_pct: r.fs_available_bytes != null && r.fs_total_bytes != null && r.fs_total_bytes > 0
          ? Math.round((r.fs_available_bytes / r.fs_total_bytes) * 100) : null,
        disk_avail_gb: r.fs_available_bytes != null ? +(r.fs_available_bytes / 1e9).toFixed(2) : null,
      }));

      const payload = { tool: "cluster_health_summary", lookback, clusters };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── red_yellow_periods ─────────────────────────────────────────────────
  registerAppTool(
    server,
    "red_yellow_periods",
    {
      title: "Red / Yellow Periods",
      description: "Track when the cluster degraded to yellow or red status and for how long. Shows a timeline of health events.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows<YellowRedRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "cluster_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.cluster.stats.status IS NOT NULL
| WHERE elasticsearch.cluster.stats.status != "green"
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS samples = COUNT(), node_count = MAX(elasticsearch.cluster.stats.nodes.count), shards_count = MAX(elasticsearch.cluster.stats.indices.shards.count) BY bucket = DATE_TRUNC(10 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.cluster.stats.status
| SORT bucket DESC, samples DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );

      const periods = rows.map((r) => ({
        bucket: r.bucket,
        cluster_name: r["elasticsearch.cluster.name"],
        status: r["elasticsearch.cluster.stats.status"],
        samples: r.samples,
        node_count: r.node_count,
        shards: r.shards_count,
      }));

      const payload = { tool: "red_yellow_periods", lookback, periods };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── node_last_seen ─────────────────────────────────────────────────────
  registerAppTool(
    server,
    "node_last_seen",
    {
      title: "Node Last Seen",
      description: "Show the most recent monitoring sample for each node. Identifies nodes that may have dropped out.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<NodeLastSeenRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR elasticsearch.node.name == ?node_name)
| STATS last_seen = MAX(@timestamp), samples = COUNT() BY elasticsearch.cluster.name, elasticsearch.node.name
| SORT last_seen ASC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const nodes = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        node_name: r["elasticsearch.node.name"],
        last_seen: r.last_seen,
        samples: r.samples,
      }));

      const payload = { tool: "node_last_seen", lookback, nodes };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── node_pressure_summary ──────────────────────────────────────────────
  registerAppTool(
    server,
    "node_pressure_summary",
    {
      title: "Node Pressure Summary",
      description: "Identify nodes with heap, CPU, disk, rejections, or merge pressure. Shows per-node pressure scores with bar visualizations.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<NodePressureRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR elasticsearch.node.name == ?node_name)
| STATS avg_heap_pct = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct), max_heap_pct = MAX(elasticsearch.node.stats.jvm.mem.heap.used.pct), avg_cpu_pct = AVG(elasticsearch.node.stats.process.cpu.pct), max_cpu_pct = MAX(elasticsearch.node.stats.process.cpu.pct), min_available_bytes = MIN(elasticsearch.node.stats.fs.total.available_in_bytes), max_total_bytes = MAX(elasticsearch.node.stats.fs.total.total_in_bytes), indexing_pressure_bytes = MAX(elasticsearch.node.stats.indexing_pressure.memory.current.all.bytes), merge_total = MAX(elasticsearch.node.stats.indices.merges.total.count), write_rejected = MAX(elasticsearch.node.stats.thread_pool.write.rejected.count), search_rejected = MAX(elasticsearch.node.stats.thread_pool.search.rejected.count), bulk_rejected = MAX(elasticsearch.node.stats.thread_pool.bulk.rejected.count), segments_count = MAX(elasticsearch.node.stats.indices.segments.count) BY elasticsearch.cluster.name, elasticsearch.node.name
| EVAL used_pct = CASE(max_total_bytes > 0, 1.0 - TO_DOUBLE(min_available_bytes) / max_total_bytes, NULL), pressure_score = COALESCE(indexing_pressure_bytes, 0) + COALESCE(merge_total, 0) * 100 + COALESCE(write_rejected, 0) * 1000 + COALESCE(search_rejected, 0) * 1000 + COALESCE(bulk_rejected, 0) * 1000 + COALESCE(segments_count, 0)
| SORT pressure_score DESC, max_heap_pct DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const nodes = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        node_name: r["elasticsearch.node.name"],
        avg_heap_pct: r.avg_heap_pct != null ? Math.round(r.avg_heap_pct * 100) : null,
        max_heap_pct: r.max_heap_pct != null ? Math.round(r.max_heap_pct * 100) : null,
        avg_cpu_pct: r.avg_cpu_pct != null ? Math.round(r.avg_cpu_pct * 100) : null,
        max_cpu_pct: r.max_cpu_pct != null ? Math.round(r.max_cpu_pct * 100) : null,
        disk_used_pct: r.used_pct != null ? Math.round(r.used_pct * 100) : null,
        write_rejected: r.write_rejected,
        search_rejected: r.search_rejected,
        segments_count: r.segments_count,
        pressure_score: r.pressure_score != null ? Math.round(r.pressure_score) : null,
      }));

      const payload = { tool: "node_pressure_summary", lookback, nodes };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── jvm_gc_pressure ────────────────────────────────────────────────────
  registerAppTool(
    server,
    "jvm_gc_pressure",
    {
      title: "JVM GC Pressure",
      description: "Detect nodes with heap pressure and elevated GC activity. Shows old-gen vs young-gen GC time and collection counts.",
      inputSchema: { lookback, cluster_name: clusterName, node_name: nodeName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, node_name }) => {
      const rows = await safeEsqlRows<JvmGcRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "node_stats"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.node.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?node_name == "" OR elasticsearch.node.name == ?node_name)
| STATS avg_heap_pct = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct), max_heap_pct = MAX(elasticsearch.node.stats.jvm.mem.heap.used.pct), max_cpu_pct = MAX(elasticsearch.node.stats.process.cpu.pct), young_gc_count = MAX(elasticsearch.node.stats.jvm.gc.collectors.young.collection.count), young_gc_ms = MAX(elasticsearch.node.stats.jvm.gc.collectors.young.collection.ms), old_gc_count = MAX(elasticsearch.node.stats.jvm.gc.collectors.old.collection.count), old_gc_ms = MAX(elasticsearch.node.stats.jvm.gc.collectors.old.collection.ms) BY elasticsearch.cluster.name, elasticsearch.node.name
| EVAL gc_pressure_score = max_heap_pct + COALESCE(old_gc_ms, 0) / 1000 + COALESCE(young_gc_ms, 0) / 1000 + COALESCE(max_cpu_pct, 0)
| SORT gc_pressure_score DESC, max_heap_pct DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", node_name: node_name ?? "" }
        )
      );

      const nodes = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        node_name: r["elasticsearch.node.name"],
        avg_heap_pct: r.avg_heap_pct != null ? Math.round(r.avg_heap_pct * 100) : null,
        max_heap_pct: r.max_heap_pct != null ? Math.round(r.max_heap_pct * 100) : null,
        max_cpu_pct: r.max_cpu_pct != null ? Math.round(r.max_cpu_pct * 100) : null,
        young_gc_count: r.young_gc_count,
        young_gc_ms: r.young_gc_ms,
        old_gc_count: r.old_gc_count,
        old_gc_ms: r.old_gc_ms,
        gc_pressure_score: r.gc_pressure_score != null ? Math.round(r.gc_pressure_score) : null,
      }));

      const payload = { tool: "jvm_gc_pressure", lookback, nodes };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── registerAppResource: serves the compiled HTML view ─────────────────
  registerAppResource(
    server,
    "cluster-health-view",
    RESOURCE_URI,
    { mimeType: RESOURCE_MIME_TYPE },
    async () => {
      const viewPath = resolveViewPath("cluster-health");
      const html = fs.readFileSync(viewPath, "utf-8");
      return {
        contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }],
      };
    }
  );
}
