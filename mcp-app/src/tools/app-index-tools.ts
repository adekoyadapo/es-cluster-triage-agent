import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerAppTool } from "@modelcontextprotocol/ext-apps/server";
import { z } from "zod";
import { buildQuery, safeEsqlRows } from "../elastic/esql.js";

const lookback = z.string().describe('Lookback window as ES|QL time span text (e.g. "1 hour", "6 hours", "24 hours").');
const clusterName = z.string().optional().default("").describe("Optional cluster name filter; use an empty string to query all clusters.");
const indexPattern = z.string().describe("Index name or wildcard pattern to investigate (e.g. logs-app.payments-default, *orders-ingest*). Required.");

export function registerAppIndexTools(server: McpServer): void {

  // ── app_index_active_ds_indices ────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_active_ds_indices",
    {
      title: "Active Data Stream Indices",
      description: "Discover application data streams visible in the monitoring stream by grouping .ds-* backing indices back to their parent stream name.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: {},
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.index.name LIKE ".ds-*"
| WHERE NOT elasticsearch.index.name LIKE ".ds-.logs-elasticsearch*"
| WHERE NOT elasticsearch.index.name LIKE ".ds-.monitoring*"
| WHERE NOT elasticsearch.index.name LIKE ".ds-.kibana*"
| WHERE NOT elasticsearch.index.name LIKE ".ds-.security*"
| WHERE NOT elasticsearch.index.name LIKE ".ds-.ilm-history*"
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| EVAL ds_name = SUBSTRING(elasticsearch.index.name, 5, LENGTH(elasticsearch.index.name) - 22)
| STATS backing_indices = COUNT_DISTINCT(elasticsearch.index.name), max_segments = MAX(elasticsearch.index.total.segments.count), max_docs_per_backing = MAX(elasticsearch.index.total.docs.count), last_activity = MAX(@timestamp) BY ds_name, elasticsearch.cluster.name
| SORT backing_indices DESC
| LIMIT 30`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );
      const payload = { tool: "app_index_active_ds_indices", lookback, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_active_log_indices ───────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_active_log_indices",
    {
      title: "Active Log Indices",
      description: "Discover application indices that have appeared in the Elasticsearch log stream recently.",
      inputSchema: { lookback, cluster_name: clusterName },
      _meta: {},
    },
    async ({ lookback, cluster_name }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset LIKE "elasticsearch.*"
| WHERE elasticsearch.index.name IS NOT NULL
| WHERE NOT elasticsearch.index.name LIKE ".kibana*"
| WHERE NOT elasticsearch.index.name LIKE ".security*"
| WHERE NOT elasticsearch.index.name LIKE ".inference*"
| WHERE NOT elasticsearch.index.name LIKE ".secrets*"
| WHERE NOT elasticsearch.index.name LIKE ".monitoring*"
| WHERE NOT elasticsearch.index.name LIKE ".ilm-history*"
| WHERE NOT elasticsearch.index.name LIKE ".logs-elasticsearch*"
| WHERE NOT elasticsearch.index.name LIKE ".kibana_task_manager*"
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS log_events = COUNT(*), datasets = COUNT_DISTINCT(event.dataset), last_activity = MAX(@timestamp) BY elasticsearch.index.name, elasticsearch.cluster.name
| SORT log_events DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "" }
        )
      );
      const payload = { tool: "app_index_active_log_indices", lookback, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_circuit_breaker_trips ───────────────────────────────────────
  registerAppTool(
    server,
    "app_index_circuit_breaker_trips",
    {
      title: "Circuit Breaker Trips",
      description: "Find circuit-breaker trips (field data, request, in-flight) from the server logs for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (message LIKE "*circuit_breaking_exception*" OR message LIKE "*CircuitBreakerException*" OR message LIKE "*circuit breaker*" OR message LIKE "*field data*too large*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name, log.level
| SORT bucket DESC, count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_circuit_breaker_trips", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_ilm_step_errors ──────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_ilm_step_errors",
    {
      title: "ILM Step Errors",
      description: "Find ILM step errors, stuck-phase log entries, and lifecycle runner errors for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (log.logger LIKE "*IndexLifecycleRunner*" OR log.logger LIKE "*ILMHistoryStore*" OR message LIKE "*ILM*error*" OR message LIKE "*lifecycle*failed*" OR message LIKE "*policy*stuck*" OR message LIKE "*step_info*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| KEEP @timestamp, elasticsearch.cluster.name, elasticsearch.index.name, log.level, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_ilm_step_errors", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_admin_actions ────────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_admin_actions",
    {
      title: "Index Admin Actions",
      description: "Show sensitive administrative audit events (create/delete/mapping/settings/alias) for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset LIKE "elasticsearch.audit*"
| WHERE event.action IN ("create_index", "delete_index", "put_mapping", "update_settings", "add_alias", "remove_alias", "indices:admin/create", "indices:admin/delete", "indices:admin/mapping/put", "indices:admin/settings/update", "indices:admin/aliases")
| EVAL url_index = SUBSTRING(url.original, 2)
| WHERE (elasticsearch.index.name LIKE ?index_pattern OR url_index LIKE ?index_pattern)
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| KEEP @timestamp, elasticsearch.cluster.name, event.action, user.name, url.original, source.ip
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_admin_actions", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_audit_access_denied ──────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_audit_access_denied",
    {
      title: "Audit Access Denied",
      description: "Count access_denied audit events targeting the specified index, grouped by user, IP, and time bucket.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset LIKE "elasticsearch.audit*"
| WHERE event.action == "access_denied"
| EVAL url_index = SUBSTRING(url.original, 2)
| WHERE (elasticsearch.index.name LIKE ?index_pattern OR url_index LIKE ?index_pattern)
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY bucket = DATE_TRUNC(5 minutes, @timestamp), user.name, source.ip, event.action, url.original
| SORT bucket DESC, count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_audit_access_denied", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_deprecation_warnings ────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_deprecation_warnings",
    {
      title: "Deprecation Warnings",
      description: "Find deprecation log entries scoped to the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.deprecation"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY elasticsearch.cluster.name, elasticsearch.index.name, message
| SORT count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_deprecation_warnings", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_field_count_trend ────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_field_count_trend",
    {
      title: "Field Count Trend",
      description: "Measure fielddata memory, doc-values memory, segment memory, and segment count growth over time for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS fielddata_memory_bytes = MAX(elasticsearch.index.total.fielddata.memory_size_in_bytes), fielddata_evictions = MAX(elasticsearch.index.total.fielddata.evictions), segments_count = MAX(elasticsearch.index.total.segments.count), doc_values_memory_bytes = MAX(elasticsearch.index.total.segments.doc_values_memory_in_bytes), segment_memory_bytes = MAX(elasticsearch.index.total.segments.memory_in_bytes) BY bucket = DATE_TRUNC(1 hour, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name
| EVAL mapping_pressure_score = COALESCE(fielddata_memory_bytes, 0) + COALESCE(doc_values_memory_bytes, 0) + COALESCE(segment_memory_bytes, 0) + COALESCE(fielddata_evictions, 0) * 1000000 + COALESCE(segments_count, 0) * 100
| SORT mapping_pressure_score DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_field_count_trend", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_indexing_failures ────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_indexing_failures",
    {
      title: "Indexing Failures",
      description: "Find ERROR and WARN level server log entries for the target index — surfaces indexing failures and bulk errors.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE log.level IN ("ERROR", "WARN")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name, log.level, error.type
| SORT bucket DESC, count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_indexing_failures", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_recovery_events ──────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_recovery_events",
    {
      title: "Recovery Events",
      description: "Find server log entries about shard recovery activity for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (message LIKE "*recovery*" OR message LIKE "*RecoverySource*" OR message LIKE "*peer recovery*" OR message LIKE "*index recovery*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| KEEP @timestamp, elasticsearch.cluster.name, elasticsearch.index.name, log.level, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_recovery_events", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_segment_trend ────────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_segment_trend",
    {
      title: "Segment Trend",
      description: "Time-series trend of segment count, indexing latency, and fielddata memory in 15-minute buckets for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS
    segments_count      = MAX(elasticsearch.index.total.segments.count),
    index_time_ms       = MAX(elasticsearch.index.total.indexing.index_time_in_millis),
    fielddata_bytes     = MAX(elasticsearch.index.total.fielddata.memory_size_in_bytes),
    docs_count          = MAX(elasticsearch.index.total.docs.count),
    search_query_total  = MAX(elasticsearch.index.total.search.query_total)
  BY bucket = DATE_TRUNC(15 minutes, @timestamp),
     elasticsearch.cluster.name,
     elasticsearch.index.name
| SORT elasticsearch.index.name ASC, bucket ASC
| LIMIT 200`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_segment_trend", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_shard_distribution ──────────────────────────────────────────
  // No lookback param — hardcodes NOW() - 10 minutes for current state
  registerAppTool(
    server,
    "app_index_shard_distribution",
    {
      title: "Shard Distribution",
      description: "Shows the current (last 10 minutes) shard distribution across nodes. A non-zero unassigned count is a live problem.",
      inputSchema: { cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - 10 minutes
| WHERE metricset.name == "shard"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| EVAL is_primary     = CASE(elasticsearch.shard.primary == true AND elasticsearch.shard.state == "STARTED", 1, 0)
| EVAL is_replica     = CASE(elasticsearch.shard.primary == false AND elasticsearch.shard.state == "STARTED", 1, 0)
| EVAL is_unassigned  = CASE(elasticsearch.shard.state == "UNASSIGNED", 1, 0)
| EVAL is_init        = CASE(elasticsearch.shard.state == "INITIALIZING" OR elasticsearch.shard.state == "RELOCATING", 1, 0)
| STATS
    primaries    = SUM(is_primary),
    replicas     = SUM(is_replica),
    unassigned   = SUM(is_unassigned),
    transitioning = SUM(is_init)
  BY elasticsearch.node.name, elasticsearch.cluster.name
| SORT primaries DESC`,
          { cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_shard_distribution", index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_status_summary ───────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_status_summary",
    {
      title: "Index Status Summary",
      description: "Snapshot per-index document counts, store size, indexing throughput, indexing time, and segment counts in 5-minute buckets.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM .monitoring-es-*
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE metricset.name == "index"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS docs_count = MAX(elasticsearch.index.total.docs.count), docs_deleted = MAX(elasticsearch.index.total.docs.deleted), store_bytes = MAX(elasticsearch.index.total.store.size_in_bytes), index_total = MAX(elasticsearch.index.total.indexing.index_total), index_time_ms = MAX(elasticsearch.index.total.indexing.index_time_in_millis), search_query_total = MAX(elasticsearch.index.total.search.query_total), fielddata_memory_bytes = MAX(elasticsearch.index.total.fielddata.memory_size_in_bytes), segments_count = MAX(elasticsearch.index.total.segments.count) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name
| SORT bucket DESC, store_bytes DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_status_summary", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_unassigned_events ────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_unassigned_events",
    {
      title: "Unassigned Shard Events",
      description: "Find server log entries about shard allocation failures, unassigned shards, and NODE_LEFT / NO_VALID_SHARD_COPY events for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (message LIKE "*failed to allocate*" OR message LIKE "*unassigned*" OR message LIKE "*cannot allocate*" OR message LIKE "*NODE_LEFT*" OR message LIKE "*NO_VALID_SHARD_COPY*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| KEEP @timestamp, elasticsearch.cluster.name, elasticsearch.index.name, log.level, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_unassigned_events", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_write_rejections ─────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_write_rejections",
    {
      title: "Write Rejections",
      description: "Find bulk and write thread-pool rejection log entries scoped to the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (message LIKE "*rejected execution*" OR message LIKE "*EsRejectedExecutionException*" OR message LIKE "*bulk*rejected*" OR message LIKE "*write*rejected*" OR message LIKE "*indexing pressure*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY bucket = DATE_TRUNC(5 minutes, @timestamp), elasticsearch.cluster.name, elasticsearch.index.name, log.level
| SORT bucket DESC, count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_write_rejections", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_mapping_error_log ────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_mapping_error_log",
    {
      title: "Mapping Error Log",
      description: "Find mapper_parsing_exception and total-fields-limit errors from the server logs for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (log.logger LIKE "*MapperService*" OR log.logger LIKE "*MapperParsingException*" OR message LIKE "*total fields*" OR message LIKE "*mapper_parsing_exception*" OR message LIKE "*mapping explosion*" OR message LIKE "*dynamic mapping*" OR message LIKE "*field limit*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*) BY elasticsearch.index.name, error.type, log.level
| SORT count DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_mapping_error_log", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_rollover_failure_log ─────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_rollover_failure_log",
    {
      title: "Rollover Failure Log",
      description: "Find rollover failures, broken write-alias entries, and data stream rollover errors for the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.server"
| WHERE (message LIKE "*rollover*failed*" OR message LIKE "*rollover*error*" OR message LIKE "*is_write_index*" OR message LIKE "*write alias*" OR message LIKE "*data_stream*rollover*" OR message LIKE "*RolloverAction*")
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| KEEP @timestamp, elasticsearch.cluster.name, elasticsearch.index.name, log.level, message
| SORT @timestamp DESC
| LIMIT 50`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_rollover_failure_log", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );

  // ── app_index_slow_log ─────────────────────────────────────────────────────
  registerAppTool(
    server,
    "app_index_slow_log",
    {
      title: "Slow Log",
      description: "Count slow-log entries for search and indexing operations on the target index.",
      inputSchema: { lookback, cluster_name: clusterName, index_pattern: indexPattern },
      _meta: {},
    },
    async ({ lookback, cluster_name, index_pattern }) => {
      const rows = await safeEsqlRows(
        buildQuery(
          `FROM elastic-cloud-logs-8
| WHERE @timestamp >= NOW() - TO_TIMEDURATION(?lookback)
| WHERE event.dataset == "elasticsearch.slowlog"
| WHERE elasticsearch.index.name LIKE ?index_pattern
| WHERE (?cluster_name == "" OR elasticsearch.cluster.name == ?cluster_name)
| STATS count = COUNT(*), latest = MAX(@timestamp) BY elasticsearch.cluster.name, elasticsearch.index.name, log.logger
| SORT count DESC
| LIMIT 20`,
          { lookback, cluster_name: cluster_name ?? "", index_pattern }
        )
      );
      const payload = { tool: "app_index_slow_log", lookback, index_pattern, rows };
      return { content: [{ type: "text" as const, text: JSON.stringify(payload) }], structuredContent: payload };
    }
  );
}
