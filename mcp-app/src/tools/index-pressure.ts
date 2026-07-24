import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://index-pressure/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const indexName = z.string().optional().default("").describe("Optional index name filter; use an empty string to query all indices.");

interface IndexPressureRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  indexing_total: number | null;
  indexing_time_ms: number | null;
  search_total: number | null;
  search_time_ms: number | null;
  docs_total: number | null;
  segments_count: number | null;
  store_size_bytes: number | null;
  shards_total: number | null;
  fielddata_memory_bytes: number | null;
  query_cache_evictions: number | null;
  pressure_score: number | null;
  avg_index_latency_ms: number | null;
  avg_search_latency_ms: number | null;
}

interface UnassignedShardRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  "elasticsearch.shard.state": string;
  "elasticsearch.shard.source_node.name": string | null;
  shards: number;
  first_seen: string;
  last_seen: string;
}

interface HotShardRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  docs_total: number | null;
  store_size_bytes: number | null;
  shards_total: number | null;
  primaries_total: number | null;
  segments_count: number | null;
  indexing_total: number | null;
  search_total: number | null;
  avg_shard_size_bytes: number | null;
  hot_shard_score: number | null;
}

interface SlowQueryRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  search_total: number | null;
  search_time_ms: number | null;
  docs_total: number | null;
  segments_count: number | null;
  avg_query_latency_ms: number | null;
}

export function registerIndexPressureTools(server: McpServer): void {

  // ── index_pressure_analysis ────────────────────────────────────────────
  registerAppTool(
    server,
    "index_pressure_analysis",
    {
      title: "Index Pressure Analysis",
      description: "Identify indices causing cluster pressure through load, latency, shard growth, cache pressure, or storage growth.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<IndexPressureRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS indexing_total = MAX(elasticsearch.index.total.indexing.index_total), indexing_time_ms = MAX(elasticsearch.index.total.indexing.index_time_in_millis), search_total = MAX(elasticsearch.index.total.search.query_total), search_time_ms = MAX(elasticsearch.index.total.search.query_time_in_millis), docs_total = MAX(elasticsearch.index.total.docs.count), segments_count = MAX(elasticsearch.index.total.segments.count), store_size_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), shards_total = MAX(elasticsearch.index.shards.total), fielddata_memory_bytes = MAX(elasticsearch.index.total.fielddata.memory_size_in_bytes), query_cache_evictions = MAX(elasticsearch.index.total.query_cache.evictions) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL avg_index_latency_ms = CASE(indexing_total > 0, ROUND(TO_DOUBLE(indexing_time_ms) / indexing_total, 2), 0), avg_search_latency_ms = CASE(search_total > 0, ROUND(TO_DOUBLE(search_time_ms) / search_total, 2), 0), pressure_score = indexing_total + search_total + COALESCE(segments_count, 0) * 100 + COALESCE(shards_total, 0) * 100 + COALESCE(fielddata_memory_bytes, 0) / 1048576 + COALESCE(query_cache_evictions, 0) * 1000000
| SORT pressure_score DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        indexing_total: r.indexing_total ?? 0,
        search_total: r.search_total ?? 0,
        docs_total: r.docs_total ?? 0,
        segments: r.segments_count ?? 0,
        shards: r.shards_total ?? 0,
        store_size_mb: r.store_size_bytes != null ? +(r.store_size_bytes / 1e6).toFixed(1) : null,
        avg_index_latency_ms: r.avg_index_latency_ms != null ? Math.round(r.avg_index_latency_ms) : null,
        avg_search_latency_ms: r.avg_search_latency_ms != null ? Math.round(r.avg_search_latency_ms) : null,
        pressure_score: r.pressure_score != null ? Math.round(r.pressure_score) : 0,
      }));

      const payload = { tool: "index_pressure_analysis", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── unassigned_shard_analysis ──────────────────────────────────────────
  registerAppTool(
    server,
    "unassigned_shard_analysis",
    {
      title: "Unassigned Shard Analysis",
      description: "Find shard states that are not STARTED and may indicate allocation issues. Shows affected indices and how long shards have been unassigned.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<UnassignedShardRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "shard"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE elasticsearch.shard.state != "STARTED"
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS shards = COUNT(), first_seen = MIN(@timestamp), last_seen = MAX(@timestamp) BY elasticsearch.cluster.name, elasticsearch.index.name, elasticsearch.shard.state, elasticsearch.shard.source_node.name
| SORT shards DESC, last_seen DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const shards = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        state: r["elasticsearch.shard.state"],
        source_node: r["elasticsearch.shard.source_node.name"],
        count: r.shards,
        first_seen: r.first_seen,
        last_seen: r.last_seen,
      }));

      const payload = { tool: "unassigned_shard_analysis", lookback, shards };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── hot_shards_analysis ────────────────────────────────────────────────
  registerAppTool(
    server,
    "hot_shards_analysis",
    {
      title: "Hot Shards Analysis",
      description: "Identify indices with disproportionately large shard footprints or shard imbalance signals.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<HotShardRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS docs_total = MAX(elasticsearch.index.total.docs.count), store_size_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), shards_total = MAX(elasticsearch.index.shards.total), primaries_total = MAX(elasticsearch.index.shards.primaries), segments_count = MAX(elasticsearch.index.total.segments.count), indexing_total = MAX(elasticsearch.index.total.indexing.index_total), search_total = MAX(elasticsearch.index.total.search.query_total) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL avg_shard_size_bytes = CASE(shards_total > 0, store_size_bytes / shards_total, store_size_bytes), hot_shard_score = CASE(shards_total > 0, store_size_bytes / shards_total, store_size_bytes) + COALESCE(segments_count, 0) * 1000 + COALESCE(indexing_total, 0) + COALESCE(search_total, 0)
| SORT hot_shard_score DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        docs: r.docs_total ?? 0,
        store_mb: r.store_size_bytes != null ? +(r.store_size_bytes / 1e6).toFixed(1) : null,
        shards: r.shards_total ?? 0,
        primaries: r.primaries_total ?? 0,
        segments: r.segments_count ?? 0,
        avg_shard_mb: r.avg_shard_size_bytes != null ? +(r.avg_shard_size_bytes / 1e6).toFixed(1) : null,
        hot_score: r.hot_shard_score != null ? Math.round(r.hot_shard_score) : 0,
      }));

      const payload = { tool: "hot_shards_analysis", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── slow_index_queries ─────────────────────────────────────────────────
  registerAppTool(
    server,
    "slow_index_queries",
    {
      title: "Slow Index Queries",
      description: "Find indices with the highest average query latency or sustained search load.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<SlowQueryRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS search_total = MAX(elasticsearch.index.total.search.query_total), search_time_ms = MAX(elasticsearch.index.total.search.query_time_in_millis), docs_total = MAX(elasticsearch.index.total.docs.count), segments_count = MAX(elasticsearch.index.total.segments.count) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL avg_query_latency_ms = CASE(search_total > 0, ROUND(TO_DOUBLE(search_time_ms) / search_total, 2), 0)
| SORT avg_query_latency_ms DESC, search_total DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        search_total: r.search_total ?? 0,
        search_time_ms: r.search_time_ms ?? 0,
        docs: r.docs_total ?? 0,
        segments: r.segments_count ?? 0,
        avg_latency_ms: r.avg_query_latency_ms != null ? Math.round(r.avg_query_latency_ms) : 0,
      }));

      const payload = { tool: "slow_index_queries", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── registerAppResource: serves the compiled HTML view ─────────────────
  registerAppResource(
    server,
    "index-pressure-view",
    RESOURCE_URI,
    { mimeType: RESOURCE_MIME_TYPE },
    async () => {
      const viewPath = resolveViewPath("index-pressure");
      const html = fs.readFileSync(viewPath, "utf-8");
      return {
        contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }],
      };
    }
  );
}
