import React, { useRef, useState } from "react";
import { useApp, AppLike, ToolResultParams } from "@shared/use-app";
import { parseToolResult } from "@shared/parse-tool-result";
import { theme } from "@shared/theme";
import {
  ScatterPlot, ScatterPoint,
  HBarChart, HBarItem,
  Histogram, HistogramBucket,
  ViewHeader, StatRow, SectionCard,
  InvestigationActions, InvAction,
} from "@shared/charts";

interface IndexEntry {
  cluster_name: string; index_name: string;
  indexing_total: number; search_total: number; docs_total: number;
  segments: number; shards: number; store_size_mb: number | null;
  avg_index_latency_ms: number | null; avg_search_latency_ms: number | null; pressure_score: number;
}
interface ShardEntry {
  cluster_name: string; index_name: string; state: string;
  source_node: string | null; count: number; first_seen: string; last_seen: string;
}
interface HotShard {
  cluster_name: string; index_name: string;
  docs: number; store_mb: number | null; shards: number; primaries: number;
  segments: number; avg_shard_mb: number | null; hot_score: number;
}
interface SlowQuery {
  cluster_name: string; index_name: string;
  search_ops: number; docs: number; segments: number; avg_latency_ms: number;
}

type Payload =
  | { tool: "index_pressure_analysis"; lookback: string; indices: IndexEntry[] }
  | { tool: "unassigned_shard_analysis"; lookback: string; shards: ShardEntry[] }
  | { tool: "hot_shards_analysis"; lookback: string; indices: HotShard[] }
  | { tool: "slow_index_queries"; lookback: string; indices: SlowQuery[] };

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

  if (!payload) return <div className="ds-view" style={{ color: theme.textMuted, fontSize: 12 }}>Waiting for index pressure data…</div>;

  const title = payload.tool.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="ds-view">
      <ViewHeader title={title} subtitle={`index pressure · ${payload.lookback}`} />

      {payload.tool === "index_pressure_analysis" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No index pressure detected.</div>;

        const maxSearch = Math.max(...payload.indices.map(i => i.search_total), 1);
        const maxIndexing = Math.max(...payload.indices.map(i => i.indexing_total), 1);

        // Scatter: indexing ops vs search ops, sized by store
        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => ({
          x: idx.indexing_total,
          y: idx.search_total,
          label: idx.index_name,
          size: Math.max(4, Math.min(14, (idx.store_size_mb ?? 100) / 500)),
          color: idx.pressure_score > 5000 ? "#F85149" : idx.pressure_score > 1000 ? "#F0883E" : "#00BFB3",
        }));

        const pressureItems: HBarItem[] = payload.indices.slice(0, 15).map(idx => ({
          label: idx.index_name,
          value: idx.pressure_score,
          sub: `${idx.shards} shards · ${idx.store_size_mb}MB`,
          color: idx.pressure_score > 5000 ? "#F85149" : idx.pressure_score > 1000 ? "#F0883E" : "#00BFB3",
        }));

        const highLatency = payload.indices.filter(i => (i.avg_search_latency_ms ?? 0) > 100);

        const invActions: InvAction[] = [
          { label: "Check hot shards", prompt: `Run hot_shards_analysis for the last ${payload.lookback}`, icon: "🔥" },
          { label: "Slow queries", prompt: `Run slow_index_queries for the last ${payload.lookback}`, icon: "⚡" },
          { label: "Unassigned shards", prompt: `Run unassigned_shard_analysis for the last ${payload.lookback}`, icon: "⚠️" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Indices", value: payload.indices.length },
              { label: "High Latency", value: highLatency.length, color: highLatency.length > 0 ? theme.orange : theme.statusGreen },
              { label: "Max Score", value: Math.max(...payload.indices.map(i => i.pressure_score)).toLocaleString(), color: theme.red },
            ]} />
            <SectionCard title="Indexing vs Search Volume">
              <ScatterPlot points={scatterPoints} xLabel="Indexing Ops" yLabel="Search Ops" height={180} />
            </SectionCard>
            <SectionCard title="Pressure Score Ranking">
              <HBarChart items={pressureItems} />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "unassigned_shard_analysis" && (() => {
        if (!payload.shards.length) return (
          <div style={{ color: theme.statusGreen, padding: 12, fontSize: 13, fontWeight: 600 }}>
            ✓ All shards STARTED — no allocation issues.
          </div>
        );

        const byState: Record<string, number> = {};
        for (const s of payload.shards) byState[s.state] = (byState[s.state] ?? 0) + s.count;

        const stateItems: HBarItem[] = Object.entries(byState).map(([state, count]) => ({
          label: state,
          value: count,
          color: state === "UNASSIGNED" ? theme.red : theme.orange,
        }));

        const invActions: InvAction[] = [
          { label: "Disk pressure", prompt: `Run disk_shard_pressure for the last ${payload.lookback}`, icon: "💾" },
          { label: "Index pressure", prompt: `Run index_pressure_analysis for the last ${payload.lookback}`, icon: "📊" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Unassigned Groups", value: payload.shards.length, color: theme.red },
              { label: "Total Unassigned", value: payload.shards.reduce((s, e) => s + e.count, 0), color: theme.red },
            ]} />
            <SectionCard title="Shard State Distribution">
              <HBarChart items={stateItems} />
            </SectionCard>
            <SectionCard title="Unassigned Shard Groups" collapsible>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: theme.textMuted, fontSize: 10, textTransform: "uppercase" }}>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>Index</th>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>State</th>
                    <th style={{ textAlign: "right", padding: "4px 8px 6px 0" }}>Count</th>
                    <th style={{ textAlign: "right", padding: "4px 0 6px 0" }}>Since</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.shards.map((s, i) => (
                    <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", color: theme.text, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>{s.index_name}</td>
                      <td style={{ padding: "4px 8px 4px 0", color: s.state === "UNASSIGNED" ? theme.red : theme.orange, fontWeight: 600 }}>{s.state}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 8px 4px 0", color: theme.textMuted }}>{s.count}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 0", color: theme.textFaint, fontSize: 10 }}>{new Date(s.first_seen).toLocaleTimeString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "hot_shards_analysis" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No hot shards detected.</div>;

        // Scatter: segments vs docs, sized by shard count
        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => ({
          x: idx.segments,
          y: idx.docs,
          label: idx.index_name,
          size: Math.max(4, Math.min(14, idx.shards * 1.5)),
          color: idx.hot_score > 5000 ? theme.red : idx.hot_score > 1000 ? theme.orange : theme.teal,
        }));

        const sizeItems: HBarItem[] = payload.indices.slice(0, 12).map(idx => ({
          label: idx.index_name,
          value: idx.store_mb ?? 0,
          sub: `${idx.shards} shards · ${idx.segments} segments`,
          color: idx.hot_score > 5000 ? theme.red : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Index pressure", prompt: `Run index_pressure_analysis for the last ${payload.lookback}`, icon: "📊" },
          { label: "Index lifecycle", prompt: `Run ilm_stuck_indices for the last ${payload.lookback}`, icon: "🔄" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Hot Indices", value: payload.indices.length, color: theme.orange },
              { label: "Total Docs", value: payload.indices.reduce((s, i) => s + i.docs, 0).toLocaleString() },
              { label: "Max Segments", value: Math.max(...payload.indices.map(i => i.segments)), color: theme.orange },
            ]} />
            <SectionCard title="Segments vs Document Count">
              <ScatterPlot points={scatterPoints} xLabel="Segments" yLabel="Docs" height={180} />
            </SectionCard>
            <SectionCard title="Store Size Ranking">
              <HBarChart items={sizeItems} unit="MB" />
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "slow_index_queries" && (() => {
        if (!payload.indices.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No slow queries detected.</div>;

        // Histogram: latency distribution buckets
        const latencyBuckets: HistogramBucket[] = payload.indices.slice(0, 20).map(idx => ({
          label: idx.index_name.split("-").slice(-1)[0] || idx.index_name,
          value: idx.avg_latency_ms,
          color: idx.avg_latency_ms > 500 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.teal,
        }));

        const scatterPoints: ScatterPoint[] = payload.indices.slice(0, 20).map(idx => ({
          x: idx.search_ops,
          y: idx.avg_latency_ms,
          label: idx.index_name,
          size: 6,
          color: idx.avg_latency_ms > 500 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "Index pressure", prompt: `Run index_pressure_analysis for the last ${payload.lookback}`, icon: "📊" },
          { label: "Hot shards", prompt: `Run hot_shards_analysis for the last ${payload.lookback}`, icon: "🔥" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Slow Indices", value: payload.indices.length, color: theme.orange },
              { label: "Max Latency", value: `${Math.max(...payload.indices.map(i => i.avg_latency_ms))}ms`, color: theme.red },
              { label: "Avg Latency", value: `${Math.round(payload.indices.reduce((s, i) => s + i.avg_latency_ms, 0) / payload.indices.length)}ms`, color: theme.orange },
            ]} />
            <SectionCard title="Query Latency Distribution (ms)">
              <Histogram buckets={latencyBuckets} height={120} yUnit="ms"
                colorFn={(v) => v > 500 ? theme.red : v > 100 ? theme.orange : theme.teal} />
            </SectionCard>
            <SectionCard title="Query Volume vs Latency">
              <ScatterPlot points={scatterPoints} xLabel="Search Ops" yLabel="Latency (ms)" yUnit="ms" height={160} />
            </SectionCard>
            <SectionCard title="Slow Query Details" collapsible>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: theme.textMuted, fontSize: 10, textTransform: "uppercase" }}>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>Index</th>
                    <th style={{ textAlign: "right", padding: "4px 8px 6px 0" }}>Avg Latency</th>
                    <th style={{ textAlign: "right", padding: "4px 8px 6px 0" }}>Queries</th>
                    <th style={{ textAlign: "right", padding: "4px 0 6px 0" }}>Segments</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.indices.map((idx, i) => (
                    <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", color: theme.text }}>{idx.index_name}</td>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", textAlign: "right", color: idx.avg_latency_ms > 500 ? theme.red : idx.avg_latency_ms > 100 ? theme.orange : theme.text }}>{idx.avg_latency_ms}ms</td>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", textAlign: "right", color: theme.textMuted }}>{idx.search_ops.toLocaleString()}</td>
                      <td className="mono" style={{ padding: "4px 0", textAlign: "right", color: theme.textFaint }}>{idx.segments}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}
    </div>
  );
}
