import React, { useRef, useState } from "react";
import { useApp, AppLike, ToolResultParams } from "@shared/use-app";
import { parseToolResult } from "@shared/parse-tool-result";
import { theme } from "@shared/theme";
import {
  AreaChart, ChartSeries,
  ScatterPlot, ScatterPoint,
  HBarChart, HBarItem,
  Histogram, HistogramBucket,
  Sparkline,
  ViewHeader, StatRow, SectionCard,
  InvestigationActions, InvAction,
} from "@shared/charts";

interface GrowthEntry { bucket: string; cluster_name: string; index_name: string; store_mb: number | null; docs: number; segments: number; indexing_ops: number; }
interface ExplosionEntry { cluster_name: string; index_name: string; fielddata_mb: number | null; fielddata_evictions: number; query_cache_evictions: number; request_cache_evictions: number; segments: number; docs: number; explosion_score: number; }
interface IlmEntry { cluster_name: string; index_name: string; docs: number; store_mb: number | null; segments: number; shards: number; indexing_ops: number; throttle_ms: number; rollover_score: number; }
interface IndexingHotspot { cluster_name: string; index_name: string; indexing_ops: number; docs: number; store_mb: number | null; shards: number; segments: number; avg_latency_ms: number; pressure_score: number; }
interface SearchHotspot { cluster_name: string; index_name: string; search_ops: number; docs: number; cache_hits: number; cache_misses: number; query_cache_evictions: number; avg_latency_ms: number; }
interface IndexTimelinePoint { ts: string; indexing: number; search: number; }
interface IndexTimeline { index_name: string; cluster_name: string; points: IndexTimelinePoint[]; }

type Payload =
  | { tool: "index_growth_analysis"; lookback: string; growth: GrowthEntry[] }
  | { tool: "mapping_explosion_detection"; lookback: string; indices: ExplosionEntry[] }
  | { tool: "ilm_stuck_indices"; lookback: string; indices: IlmEntry[] }
  | { tool: "indexing_hotspots"; lookback: string; indices: IndexingHotspot[] }
  | { tool: "search_hotspots"; lookback: string; indices: SearchHotspot[] }
  | { tool: "index_metrics_timeline"; lookback: string; indices: IndexTimeline[] };

const INDEX_COLORS = ["#00BFB3", "#0077CC", "#BC8CFF", "#F0883E", "#54B399", "#FEC514", "#F85149", "#58A6FF"];

export function App() {
  const appRef = useRef<AppLike | null>(null);
  const [payload, setPayload] = useState<Payload | null>(null);

  useApp({
    appInfo: { name: "elastic-cluster-triage-agent", version: "1.0.0" },
    onAppCreated: (app) => {
      appRef.current = app;
      app.ontoolresult = (p: ToolResultParams) => {
        const d = parseToolResult<Payload>(p);
        if (d) setPayload(d);
      };
    },
  });

  const sendMessage = (prompt: string) => appRef.current?.sendMessage?.(prompt);

  if (!payload) return <div className="ds-view" style={{ color: theme.textMuted, fontSize: 12 }}>Waiting for lifecycle data…</div>;

  const title = payload.tool.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="ds-view">
      <ViewHeader title={title} subtitle={`index lifecycle · ${payload.lookback}`} />

      {payload.tool === "index_growth_analysis" && (() => {
        if (!payload.growth.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>No growth data found.</div>;

        // Group by index
        const byIndex: Record<string, GrowthEntry[]> = {};
        for (const g of payload.growth) {
          const key = `${g.cluster_name}/${g.index_name}`;
          (byIndex[key] ??= []).push(g);
        }
        const indexKeys = Object.keys(byIndex).slice(0, 8);

        // Multi-series area chart for top indices
        const growthSeries: ChartSeries[] = indexKeys.map((key, i) => {
          const entries = byIndex[key].sort((a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime());
          return {
            label: key.split("/").slice(1).join("/"),
            color: INDEX_COLORS[i % INDEX_COLORS.length],
            points: entries.map(e => ({ ts: e.bucket, value: e.store_mb ?? 0 })),
            filled: false,
          };
        });

        // Size delta items
        const deltaItems: HBarItem[] = indexKeys.map((key, i) => {
          const entries = byIndex[key].sort((a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime());
          const latest = entries[entries.length - 1];
          const oldest = entries[0];
          const delta = (latest.store_mb ?? 0) - (oldest.store_mb ?? 0);
          return {
            label: key.split("/").slice(1).join("/"),
            value: Math.max(delta, 0),
            sub: `${(latest.store_mb ?? 0).toFixed(0)}MB now`,
            color: delta > 500 ? theme.red : delta > 100 ? theme.orange : theme.teal,
          };
        });

        const invActions: InvAction[] = [
          { label: "ILM stuck indices", prompt: `Run ilm_stuck_indices for the last ${payload.lookback}`, icon: "🔄" },
          { label: "Indexing hotspots", prompt: `Run indexing_hotspots for the last ${payload.lookback}`, icon: "🔥" },
          { label: "Mapping explosion", prompt: `Run mapping_explosion_detection for the last ${payload.lookback}`, icon: "💥" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Indices Tracked", value: Object.keys(byIndex).length },
              { label: "Max Growth", value: `+${Math.max(...deltaItems.map(d => d.value)).toFixed(0)}MB`, color: theme.orange },
            ]} />
            <SectionCard title="Store Size Trend (MB)">
              <AreaChart series={growthSeries} yUnit="MB" height={150} />
            </SectionCard>
            <SectionCard title="Growth Delta">
              <HBarChart items={deltaItems} unit="MB" />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "mapping_explosion_detection" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No mapping pressure detected.</div>;

        // Compute adaptive fielddata unit
        const maxFielddataMB = Math.max(...payload.indices.map(i => i.fielddata_mb ?? 0));
        const useKB = maxFielddataMB < 1 && maxFielddataMB > 0;
        const fieldUnit = useKB ? "KB" : "MB";
        const toFieldUnit = (mb: number | null) => useKB ? (mb ?? 0) * 1024 : (mb ?? 0);

        // Primary: explosion score ranking (always has values)
        const scoreItems: HBarItem[] = payload.indices.slice(0, 15).map(idx => ({
          label: idx.index_name,
          value: idx.explosion_score,
          sub: `${idx.segments} segs · ${(idx.fielddata_mb ?? 0).toFixed(2)}MB fd`,
          color: idx.explosion_score > 1000 ? theme.red : idx.explosion_score > 200 ? theme.orange : theme.teal,
        }));

        // Fielddata memory — adaptive units
        const fieldItems: HBarItem[] = payload.indices
          .filter(i => (i.fielddata_mb ?? 0) > 0)
          .slice(0, 15)
          .map(idx => ({
            label: idx.index_name,
            value: toFieldUnit(idx.fielddata_mb),
            sub: `${idx.fielddata_evictions} fd evictions`,
            color: toFieldUnit(idx.fielddata_mb) > (useKB ? 512 : 100) ? theme.red
                 : toFieldUnit(idx.fielddata_mb) > (useKB ? 128 : 10) ? theme.orange
                 : theme.teal,
          }));

        // Cache evictions histogram — only when data exists
        const hasEvictions = payload.indices.some(i => i.fielddata_evictions + i.query_cache_evictions > 0);
        const evictionBuckets: HistogramBucket[] = payload.indices.slice(0, 15).map(idx => ({
          label: idx.index_name.split("-").slice(-1)[0],
          value: idx.fielddata_evictions + idx.query_cache_evictions,
          color: idx.explosion_score > 1000 ? theme.red : idx.explosion_score > 200 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Index growth", prompt: `Run index_growth_analysis for the last ${payload.lookback}`, icon: "📈" },
          { label: "ILM stuck", prompt: `Run ilm_stuck_indices for the last ${payload.lookback}`, icon: "🔄" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Pressure Indices", value: payload.indices.length, color: theme.orange },
              { label: "Max Fielddata", value: `${maxFielddataMB < 1 ? `${(maxFielddataMB * 1024).toFixed(0)}KB` : `${maxFielddataMB.toFixed(0)}MB`}`, color: theme.red },
              { label: "Total FD Evictions", value: payload.indices.reduce((s, i) => s + i.fielddata_evictions, 0).toLocaleString(), color: theme.red },
              { label: "Max Score", value: Math.max(...payload.indices.map(i => i.explosion_score)).toLocaleString(), color: theme.orange },
            ]} />

            <SectionCard title="Explosion Score Ranking">
              <HBarChart items={scoreItems} />
            </SectionCard>

            {fieldItems.length > 0 ? (
              <SectionCard title={`Fielddata Memory (${fieldUnit})`}>
                <HBarChart items={fieldItems} unit={fieldUnit} />
              </SectionCard>
            ) : (
              <SectionCard title="Fielddata Memory">
                <div style={{ padding: "10px 0", fontSize: 12, color: theme.textMuted }}>✓ No fielddata memory in use — scores driven by segment count and doc cardinality.</div>
              </SectionCard>
            )}

            {hasEvictions && (
              <SectionCard title="Cache Evictions by Index">
                <Histogram buckets={evictionBuckets} height={110} yUnit=" evictions" />
              </SectionCard>
            )}

            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "ilm_stuck_indices" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No ILM candidates detected.</div>;

        const scoreItems: HBarItem[] = payload.indices.slice(0, 15).map(idx => ({
          label: idx.index_name,
          value: idx.rollover_score,
          sub: `${idx.store_mb}MB · ${idx.shards} shards`,
          color: idx.rollover_score > 100 ? theme.red : idx.rollover_score > 50 ? theme.orange : theme.teal,
        }));

        // Scatter: docs vs store_mb, colored by throttle
        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => ({
          x: idx.docs,
          y: idx.store_mb ?? 0,
          label: idx.index_name,
          size: Math.max(4, Math.min(12, idx.shards * 2)),
          color: idx.throttle_ms > 0 ? theme.red : idx.rollover_score > 50 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Index growth", prompt: `Run index_growth_analysis for the last ${payload.lookback}`, icon: "📈" },
          { label: "Disk pressure", prompt: `Run disk_shard_pressure for the last ${payload.lookback}`, icon: "💾" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "ILM Candidates", value: payload.indices.length, color: theme.orange },
              { label: "Throttled", value: payload.indices.filter(i => i.throttle_ms > 0).length, color: theme.red },
              { label: "Total Size", value: `${payload.indices.reduce((s, i) => s + (i.store_mb ?? 0), 0).toFixed(0)}MB` },
            ]} />
            <SectionCard title="Docs vs Store Size">
              <ScatterPlot points={scatterPoints} xLabel="Docs" yLabel="Size (MB)" yUnit="MB" height={160} />
            </SectionCard>
            <SectionCard title="Rollover Score Ranking">
              <HBarChart items={scoreItems} />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "indexing_hotspots" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No indexing hotspots detected.</div>;

        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => ({
          x: idx.indexing_ops,
          y: idx.avg_latency_ms,
          label: idx.index_name,
          size: Math.max(4, Math.min(14, (idx.store_mb ?? 50) / 200)),
          color: idx.pressure_score > 5000 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.teal,
        }));

        const opsItems: HBarItem[] = payload.indices.slice(0, 12).map(idx => ({
          label: idx.index_name,
          value: idx.indexing_ops,
          sub: `${idx.avg_latency_ms}ms avg`,
          color: idx.pressure_score > 5000 ? theme.red : idx.pressure_score > 1000 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Index timeline", prompt: `Run index_metrics_timeline for the last ${payload.lookback}`, icon: "📈" },
          { label: "ILM stuck indices", prompt: `Run ilm_stuck_indices for the last ${payload.lookback}`, icon: "🔄" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Hot Indices", value: payload.indices.length, color: theme.orange },
              { label: "Max Ops", value: Math.max(...payload.indices.map(i => i.indexing_ops)).toLocaleString(), color: theme.red },
              { label: "Max Latency", value: `${Math.max(...payload.indices.map(i => i.avg_latency_ms))}ms`, color: theme.orange },
            ]} />
            <SectionCard title="Indexing Ops vs Latency">
              <ScatterPlot points={scatterPoints} xLabel="Indexing Ops" yLabel="Latency (ms)" yUnit="ms" height={170} />
            </SectionCard>
            <SectionCard title="Indexing Ops Ranking">
              <HBarChart items={opsItems} />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "search_hotspots" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No search hotspots detected.</div>;

        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => {
          const hitPct = idx.cache_hits + idx.cache_misses > 0
            ? Math.round((idx.cache_hits / (idx.cache_hits + idx.cache_misses)) * 100) : 0;
          return {
            x: idx.search_ops,
            y: idx.avg_latency_ms,
            label: idx.index_name,
            size: Math.max(4, Math.min(14, (100 - hitPct) / 10)),
            color: idx.avg_latency_ms > 500 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.teal,
          };
        });

        const opsItems: HBarItem[] = payload.indices.slice(0, 12).map(idx => ({
          label: idx.index_name,
          value: idx.search_ops,
          sub: `${idx.avg_latency_ms}ms avg`,
          color: idx.avg_latency_ms > 500 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Slow queries", prompt: `Run slow_index_queries for the last ${payload.lookback}`, icon: "⚡" },
          { label: "Index timeline", prompt: `Run index_metrics_timeline for the last ${payload.lookback}`, icon: "📈" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Hot Indices", value: payload.indices.length, color: theme.orange },
              { label: "Max Latency", value: `${Math.max(...payload.indices.map(i => i.avg_latency_ms))}ms`, color: theme.red },
              { label: "Total Queries", value: payload.indices.reduce((s, i) => s + i.search_ops, 0).toLocaleString() },
            ]} />
            <SectionCard title="Search Ops vs Latency">
              <ScatterPlot points={scatterPoints} xLabel="Search Ops" yLabel="Latency (ms)" yUnit="ms" height={170} />
            </SectionCard>
            <SectionCard title="Search Volume Ranking">
              <HBarChart items={opsItems} />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "index_metrics_timeline" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.textMuted, padding: 12 }}>No index timeline data.</div>;

        const indexingSeries: ChartSeries[] = payload.indices.slice(0, 6).map((idx, i) => ({
          label: idx.index_name,
          color: INDEX_COLORS[i % INDEX_COLORS.length],
          points: idx.points.map(p => ({ ts: p.ts, value: p.indexing })),
          filled: false,
        }));

        const searchSeries: ChartSeries[] = payload.indices.slice(0, 6).map((idx, i) => ({
          label: idx.index_name,
          color: INDEX_COLORS[i % INDEX_COLORS.length],
          points: idx.points.map(p => ({ ts: p.ts, value: p.search })),
          filled: false,
        }));

        return (
          <>
            <StatRow stats={[
              { label: "Indices Tracked", value: payload.indices.length },
            ]} />
            <SectionCard title="Indexing Ops Trend">
              <AreaChart series={indexingSeries} height={140} />
            </SectionCard>
            <SectionCard title="Search Ops Trend">
              <AreaChart series={searchSeries} height={140} />
            </SectionCard>
            <SectionCard title="Per-Index Sparklines" collapsible>
              {payload.indices.map((idx, i) => {
                const indexingVals = idx.points.map(p => p.indexing);
                const searchVals = idx.points.map(p => p.search);
                return (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: `1px solid ${theme.border}` }}>
                    <span className="mono" style={{ fontSize: 11, color: INDEX_COLORS[i % INDEX_COLORS.length], width: 160, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{idx.index_name}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span style={{ fontSize: 9, color: theme.textMuted }}>idx</span>
                      <Sparkline points={indexingVals} color={theme.teal} width={56} height={18} />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span style={{ fontSize: 9, color: theme.textMuted }}>srch</span>
                      <Sparkline points={searchVals} color={theme.blue} width={56} height={18} />
                    </div>
                  </div>
                );
              })}
            </SectionCard>
          </>
        );
      })()}
    </div>
  );
}
