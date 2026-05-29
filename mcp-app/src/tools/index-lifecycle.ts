import fs from "fs";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";
import { resolveViewPath } from "./view-path.js";

const RESOURCE_URI = "ui://index-lifecycle/mcp-app.html";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "15 minutes", "1 hour", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const indexName = z.string().optional().default("").describe("Optional index name filter; use an empty string to query all indices.");

interface GrowthRow {
  bucket: string;
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  store_size_bytes: number | null;
  docs_total: number | null;
  segments_count: number | null;
  indexing_total: number | null;
}

interface ExplosionRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  fielddata_memory_bytes: number | null;
  fielddata_evictions: number | null;
  query_cache_evictions: number | null;
  request_cache_evictions: number | null;
  segments_count: number | null;
  docs_total: number | null;
  explosion_score: number | null;
}

interface IlmStuckRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  docs_total: number | null;
  store_size_bytes: number | null;
  segments_count: number | null;
  shards_total: number | null;
  indexing_total: number | null;
  throttle_time_ms: number | null;
  rollover_score: number | null;
}

interface IndexingHotspotRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  indexing_total: number | null;
  indexing_time_ms: number | null;
  docs_total: number | null;
  segments_count: number | null;
  store_size_bytes: number | null;
  shards_total: number | null;
  avg_index_latency_ms: number | null;
  pressure_score: number | null;
}

interface SearchHotspotRow {
  "elasticsearch.cluster.name": string;
  "elasticsearch.index.name": string;
  search_total: number | null;
  search_time_ms: number | null;
  docs_total: number | null;
  request_cache_hits: number | null;
  request_cache_misses: number | null;
  query_cache_evictions: number | null;
  avg_query_latency_ms: number | null;
}

export function registerIndexLifecycleTools(server: McpServer): void {

  // ── index_growth_analysis ──────────────────────────────────────────────
  registerAppTool(
    server,
    "index_growth_analysis",
    {
      title: "Index Growth Analysis",
      description: "Detect indices that are growing rapidly. Shows per-hour storage and doc count trends to identify storage runaways.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<GrowthRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS store_size_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), docs_total = MAX(elasticsearch.index.total.docs.count), segments_count = MAX(elasticsearch.index.total.segments.count), indexing_total = MAX(elasticsearch.index.total.indexing.index_total) BY bucket = DATE_TRUNC(1 hour, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name
| SORT bucket DESC, store_size_bytes DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const growth = rows.map((r) => ({
        bucket: r.bucket,
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        store_mb: r.store_size_bytes != null ? +(r.store_size_bytes / 1e6).toFixed(1) : null,
        docs: r.docs_total ?? 0,
        segments: r.segments_count ?? 0,
        indexing_ops: r.indexing_total ?? 0,
      }));

      const payload = { tool: "index_growth_analysis", lookback, growth };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── mapping_explosion_detection ────────────────────────────────────────
  registerAppTool(
    server,
    "mapping_explosion_detection",
    {
      title: "Mapping Explosion Detection",
      description: "Find indices with fielddata, cache, or segment-growth pressure that can indicate mapping explosion.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<ExplosionRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS fielddata_memory_bytes = MAX(elasticsearch.index.total.fielddata.memory_size_in_bytes), fielddata_evictions = MAX(elasticsearch.index.total.fielddata.evictions), query_cache_evictions = MAX(elasticsearch.index.total.query_cache.evictions), request_cache_evictions = MAX(elasticsearch.index.total.request_cache.evictions), segments_count = MAX(elasticsearch.index.total.segments.count), docs_total = MAX(elasticsearch.index.total.docs.count) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL explosion_score = COALESCE(fielddata_memory_bytes, 0) + COALESCE(fielddata_evictions, 0) * 1000000 + COALESCE(query_cache_evictions, 0) * 1000000 + COALESCE(request_cache_evictions, 0) * 1000000 + COALESCE(segments_count, 0) * 100
| SORT explosion_score DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        fielddata_mb: r.fielddata_memory_bytes != null ? +(r.fielddata_memory_bytes / 1e6).toFixed(2) : null,
        fielddata_evictions: r.fielddata_evictions ?? 0,
        query_cache_evictions: r.query_cache_evictions ?? 0,
        request_cache_evictions: r.request_cache_evictions ?? 0,
        segments: r.segments_count ?? 0,
        docs: r.docs_total ?? 0,
        explosion_score: r.explosion_score != null ? Math.round(r.explosion_score) : 0,
      }));

      const payload = { tool: "mapping_explosion_detection", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── ilm_stuck_indices ──────────────────────────────────────────────────
  registerAppTool(
    server,
    "ilm_stuck_indices",
    {
      title: "ILM Stuck Indices",
      description: "Find indices that look stalled in growth or throttled when ILM metadata is unavailable.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<IlmStuckRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS docs_total = MAX(elasticsearch.index.total.docs.count), store_size_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), segments_count = MAX(elasticsearch.index.total.segments.count), shards_total = MAX(elasticsearch.index.shards.total), indexing_total = MAX(elasticsearch.index.total.indexing.index_total), throttle_time_ms = MAX(elasticsearch.index.total.indexing.throttle_time_in_millis) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL rollover_score = COALESCE(docs_total, 0) + COALESCE(store_size_bytes, 0) / 1000000 + COALESCE(segments_count, 0) * 100 + COALESCE(shards_total, 0) * 1000 + COALESCE(throttle_time_ms, 0)
| SORT rollover_score DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        docs: r.docs_total ?? 0,
        store_mb: r.store_size_bytes != null ? +(r.store_size_bytes / 1e6).toFixed(1) : null,
        segments: r.segments_count ?? 0,
        shards: r.shards_total ?? 0,
        indexing_ops: r.indexing_total ?? 0,
        throttle_ms: r.throttle_time_ms ?? 0,
        rollover_score: r.rollover_score != null ? Math.round(r.rollover_score) : 0,
      }));

      const payload = { tool: "ilm_stuck_indices", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── indexing_hotspots ──────────────────────────────────────────────────
  registerAppTool(
    server,
    "indexing_hotspots",
    {
      title: "Indexing Hotspots",
      description: "Find heavy indexing volume, latency, or storage pressure by index.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<IndexingHotspotRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS indexing_total = MAX(elasticsearch.index.total.indexing.index_total), indexing_time_ms = MAX(elasticsearch.index.total.indexing.index_time_in_millis), docs_total = MAX(elasticsearch.index.total.docs.count), segments_count = MAX(elasticsearch.index.total.segments.count), store_size_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), shards_total = MAX(elasticsearch.index.shards.total) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL avg_index_latency_ms = CASE(indexing_total > 0, indexing_time_ms / indexing_total, 0), pressure_score = indexing_total + COALESCE(shards_total, 0) * 100 + COALESCE(segments_count, 0) * 100 + COALESCE(store_size_bytes, 0) / 100000000
| SORT pressure_score DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        indexing_ops: r.indexing_total ?? 0,
        docs: r.docs_total ?? 0,
        store_mb: r.store_size_bytes != null ? +(r.store_size_bytes / 1e6).toFixed(1) : null,
        shards: r.shards_total ?? 0,
        segments: r.segments_count ?? 0,
        avg_latency_ms: r.avg_index_latency_ms != null ? Math.round(r.avg_index_latency_ms) : 0,
        pressure_score: r.pressure_score != null ? Math.round(r.pressure_score) : 0,
      }));

      const payload = { tool: "indexing_hotspots", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── search_hotspots ────────────────────────────────────────────────────
  registerAppTool(
    server,
    "search_hotspots",
    {
      title: "Search Hotspots",
      description: "Find high search volume or slow searches by index. Shows cache efficiency and query latency distribution.",
      inputSchema: { lookback, cluster_name: clusterName, index_name: indexName },
      _meta: { ui: { resourceUri: RESOURCE_URI } },
    },
    async ({ lookback, cluster_name, index_name }) => {
      const rows = await safeEsqlRows<SearchHotspotRow>(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.cluster.name IS NOT NULL AND elasticsearch.index.name IS NOT NULL
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| WHERE (?index_name == "" OR elasticsearch.index.name == ?index_name)
| STATS search_total = MAX(elasticsearch.index.total.search.query_total), search_time_ms = MAX(elasticsearch.index.total.search.query_time_in_millis), docs_total = MAX(elasticsearch.index.total.docs.count), request_cache_hits = MAX(elasticsearch.index.total.request_cache.hit_count), request_cache_misses = MAX(elasticsearch.index.total.request_cache.miss_count), query_cache_evictions = MAX(elasticsearch.index.total.query_cache.evictions) BY elasticsearch.cluster.name, elasticsearch.index.name
| EVAL avg_query_latency_ms = CASE(search_total > 0, search_time_ms / search_total, 0)
| SORT avg_query_latency_ms DESC, search_total DESC
| LIMIT 25`,
          { lookback, cluster_name: cluster_name ?? "", index_name: index_name ?? "" }
        )
      );

      const indices = rows.map((r) => ({
        cluster_name: r["elasticsearch.cluster.name"],
        index_name: r["elasticsearch.index.name"],
        search_ops: r.search_total ?? 0,
        docs: r.docs_total ?? 0,
        cache_hits: r.request_cache_hits ?? 0,
        cache_misses: r.request_cache_misses ?? 0,
        query_cache_evictions: r.query_cache_evictions ?? 0,
        avg_latency_ms: r.avg_query_latency_ms != null ? Math.round(r.avg_query_latency_ms) : 0,
      }));

      const payload = { tool: "search_hotspots", lookback, indices };
      return {
        content: [{ type: "text" as const, text: JSON.stringify(payload) }],
        structuredContent: payload,
      };
    }
  );

  // ── registerAppResource: serves the compiled HTML view ─────────────────
  registerAppResource(
    server,
    "index-lifecycle-view",
    RESOURCE_URI,
    { mimeType: RESOURCE_MIME_TYPE },
    async () => {
      const viewPath = resolveViewPath("index-lifecycle");
      const html = fs.readFileSync(viewPath, "utf-8");
      return {
        contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }],
      };
    }
  );
}
