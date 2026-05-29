import React, { useRef, useState } from "react";
import { useApp, AppLike, ToolResultParams } from "@shared/use-app";
import { parseToolResult } from "@shared/parse-tool-result";
import { theme } from "@shared/theme";
import {
  AreaChart, ChartSeries,
  StatusHeatmap, HeatCell,
  Gauge,
  HBarChart, HBarItem,
  Histogram, HistogramBucket,
  Sparkline,
  ViewHeader, StatRow, SectionCard,
  InvestigationActions, InvAction,
} from "@shared/charts";

// ── Types ────────────────────────────────────────────────────────────────────

interface ClusterEntry {
  cluster_name: string; status: string; samples: number; last_seen: string;
  node_count: number | null; data_nodes: number | null; shards: number | null; primaries: number | null;
  heap_used_pct: number | null; heap_used_gb: number | null; heap_max_gb: number | null;
  disk_avail_pct: number | null; disk_avail_gb: number | null;
}
interface PeriodEntry { bucket: string; cluster_name: string; status: string; samples: number; node_count: number | null; }
interface NodeEntry { cluster_name: string; node_name: string; last_seen: string; samples: number; }
interface NodePressureEntry {
  cluster_name: string; node_name: string;
  avg_heap_pct: number | null; max_heap_pct: number | null;
  avg_cpu_pct: number | null; max_cpu_pct: number | null;
  disk_used_pct: number | null; write_rejected: number | null;
  search_rejected: number | null; segments_count: number | null; pressure_score: number | null;
}
interface GcEntry {
  cluster_name: string; node_name: string;
  avg_heap_pct: number | null; max_heap_pct: number | null; max_cpu_pct: number | null;
  young_gc_count: number | null; young_gc_ms: number | null;
  old_gc_count: number | null; old_gc_ms: number | null; gc_pressure_score: number | null;
}
interface TimelinePoint { ts: string; heap_pct: number; status: string; shards: number; nodes: number; }
interface ClusterTimeline { cluster_name: string; points: TimelinePoint[]; }
interface NodeTimelinePoint { ts: string; heap_pct: number; cpu_pct: number; }
interface NodeTimeline { node_name: string; points: NodeTimelinePoint[]; }
interface ClusterNodeTimeline { cluster_name: string; nodes: NodeTimeline[]; }

type Payload =
  | { tool: "cluster_health_summary"; lookback: string; clusters: ClusterEntry[] }
  | { tool: "red_yellow_periods"; lookback: string; periods: PeriodEntry[] }
  | { tool: "node_last_seen"; lookback: string; nodes: NodeEntry[] }
  | { tool: "node_pressure_summary"; lookback: string; nodes: NodePressureEntry[] }
  | { tool: "jvm_gc_pressure"; lookback: string; nodes: GcEntry[] }
  | { tool: "cluster_timeline"; lookback: string; clusters: ClusterTimeline[] }
  | { tool: "node_metrics_timeline"; lookback: string; clusters: ClusterNodeTimeline[] };

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Normalise a percentage value that may arrive in basis-point scale (×100).
 * ES monitoring stores heap_used_percent etc. as 0–100; if the ES|QL aggregation
 * multiplies by an extra 100, values like 8700 appear — divide back to get 87.
 * Safe to call on already-correct values (e.g. 87 → 87, 100 → 100).
 */
function normPct(v: number | null, clampMax = 100): number {
  if (v == null) return 0;
  // If value exceeds the expected max by >10 %, assume it's in a ×100 scale
  const norm = v > clampMax * 1.1 ? v / 100 : v;
  return Math.min(Math.round(norm * 10) / 10, clampMax);
}

function statusColor(s: string) {
  if (s === "green") return theme.statusGreen;
  if (s === "yellow") return theme.statusYellow;
  if (s === "red") return theme.statusRed;
  return theme.textMuted;
}

const NODE_COLORS = [theme.teal, theme.blue, theme.purple, theme.orange, theme.green, theme.yellow];

// ── Sub-views ────────────────────────────────────────────────────────────────

function ClusterHealthView({ data, onSend }: { data: { lookback: string; clusters: ClusterEntry[] }; onSend: (p: string) => void }) {
  if (!data.clusters.length) return <div style={{ color: theme.textMuted, fontSize: 12 }}>No monitoring data found for this time window.</div>;

  const totalNodes = data.clusters.reduce((s, c) => s + (c.node_count ?? 0), 0);
  const totalShards = data.clusters.reduce((s, c) => s + (c.shards ?? 0), 0);
  const maxHeap = Math.max(...data.clusters.map(c => normPct(c.heap_used_pct)));
  const unhealthy = data.clusters.filter(c => c.status !== "green").length;

  const invActions: InvAction[] = [
    { label: "Check node pressure", prompt: `Run node_pressure_summary for the last ${data.lookback}`, icon: "📊" },
    { label: "GC pressure", prompt: `Run jvm_gc_pressure for the last ${data.lookback}`, icon: "🔥" },
    { label: "View timeline", prompt: `Run cluster_timeline for the last ${data.lookback}`, icon: "📈" },
  ];

  return (
    <>
      <StatRow stats={[
        { label: "Clusters", value: data.clusters.length, color: unhealthy > 0 ? theme.orange : theme.statusGreen },
        { label: "Nodes", value: totalNodes },
        { label: "Shards", value: totalShards },
        { label: "Max Heap%", value: `${maxHeap}%`, color: maxHeap >= 90 ? theme.red : maxHeap >= 75 ? theme.orange : theme.statusGreen },
      ]} />

      {data.clusters.map((c) => (
        <SectionCard key={c.cluster_name} title={c.cluster_name}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            <span style={{
              display: "inline-block", padding: "2px 10px", borderRadius: 99,
              background: `${statusColor(c.status)}18`, color: statusColor(c.status),
              border: `1px solid ${statusColor(c.status)}55`, fontSize: 11, fontWeight: 700,
            }}>{c.status.toUpperCase()}</span>
            <span style={{ fontSize: 11, color: theme.textMuted }}>{c.node_count} nodes · {c.shards} shards</span>
          </div>
          <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap", marginBottom: 8 }}>
            {c.heap_used_pct != null && (
              <Gauge value={normPct(c.heap_used_pct)} label="Heap %" size={90}
                thresholds={[{ at: 75, color: theme.orange }, { at: 90, color: theme.red }]} />
            )}
            {c.disk_avail_pct != null && (
              <Gauge value={normPct(100 - c.disk_avail_pct)} label="Disk Used" size={90}
                thresholds={[{ at: 70, color: theme.orange }, { at: 85, color: theme.red }]} />
            )}
          </div>
          {c.heap_used_gb != null && (
            <div style={{ fontSize: 11, color: theme.textMuted, textAlign: "center", marginTop: 4 }}>
              Heap {c.heap_used_gb}GB / {c.heap_max_gb}GB · Disk {c.disk_avail_gb}GB free
            </div>
          )}
        </SectionCard>
      ))}

      <InvestigationActions actions={invActions} onSend={onSend} />
    </>
  );
}

function RedYellowView({ data, onSend }: { data: { lookback: string; periods: PeriodEntry[] }; onSend: (p: string) => void }) {
  if (!data.periods.length) return (
    <div style={{ padding: 16, textAlign: "center", color: theme.statusGreen, fontSize: 13, fontWeight: 600 }}>
      ✓ No yellow or red periods in the last {data.lookback}
    </div>
  );

  const byCluster: Record<string, PeriodEntry[]> = {};
  for (const p of data.periods) (byCluster[p.cluster_name] ??= []).push(p);

  const invActions: InvAction[] = [
    { label: "Check node last seen", prompt: `Run node_last_seen for the last ${data.lookback}`, icon: "🔍" },
    { label: "View cluster timeline", prompt: `Run cluster_timeline for the last ${data.lookback}`, icon: "📈" },
  ];

  return (
    <>
      <StatRow stats={[
        { label: "Degraded Periods", value: data.periods.length, color: theme.red },
        { label: "Clusters Affected", value: Object.keys(byCluster).length, color: theme.orange },
      ]} />

      {Object.entries(byCluster).map(([cn, periods]) => {
        const cells: HeatCell[] = periods.map(p => ({ ts: p.bucket, status: p.status }));
        return (
          <SectionCard key={cn} title={cn}>
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: theme.textMuted, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 }}>Status Timeline</div>
              <StatusHeatmap cells={cells} height={32} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 160, overflowY: "auto" }}>
              {periods.slice(0, 10).map((p, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, padding: "3px 0", borderBottom: `1px solid ${theme.border}` }}>
                  <span style={{ color: statusColor(p.status), fontWeight: 700, width: 52, flexShrink: 0 }} className="mono">{p.status}</span>
                  <span style={{ color: theme.textMuted, flex: 1 }} className="mono">{new Date(p.bucket).toLocaleTimeString()}</span>
                  <span style={{ color: theme.textFaint }}>{p.samples}×</span>
                </div>
              ))}
            </div>
          </SectionCard>
        );
      })}
      <InvestigationActions actions={invActions} onSend={onSend} />
    </>
  );
}

function NodeLastSeenView({ data }: { data: { lookback: string; nodes: NodeEntry[] } }) {
  if (!data.nodes.length) return <div style={{ color: theme.textMuted }}>No node data.</div>;

  const items: HBarItem[] = data.nodes.map(n => ({
    label: n.node_name,
    value: n.samples,
    sub: n.cluster_name,
  }));

  return (
    <SectionCard title={`Nodes (${data.nodes.length})`}>
      <div style={{ fontSize: 11, color: theme.textMuted, marginBottom: 10 }}>Monitoring sample count (more = healthier)</div>
      <HBarChart items={items} unit=" samples" colorFn={(v, max) => v < max * 0.5 ? theme.red : v < max * 0.8 ? theme.orange : theme.teal} />
    </SectionCard>
  );
}

function NodePressureView({ data, onSend }: { data: { lookback: string; nodes: NodePressureEntry[] }; onSend: (p: string) => void }) {
  if (!data.nodes.length) return <div style={{ color: theme.textMuted }}>No node data.</div>;

  const highPressure = data.nodes.filter(n => (n.pressure_score ?? 0) > 0);
  const pressureItems: HBarItem[] = data.nodes.map(n => ({
    label: n.node_name,
    value: n.pressure_score ?? 0,
    sub: n.cluster_name,
    color: (n.pressure_score ?? 0) > 5000 ? theme.red : (n.pressure_score ?? 0) > 1000 ? theme.orange : theme.teal,
  }));

  const invActions: InvAction[] = [
    { label: "Check GC pressure", prompt: `Run jvm_gc_pressure for the last ${data.lookback}`, icon: "🔥" },
    { label: "Thread pool rejections", prompt: `Run thread_pool_rejections for the last ${data.lookback}`, icon: "🚫" },
    { label: "Node timeline", prompt: `Run node_metrics_timeline for the last ${data.lookback}`, icon: "📈" },
  ];

  return (
    <>
      <StatRow stats={[
        { label: "Total Nodes", value: data.nodes.length },
        { label: "Under Pressure", value: highPressure.length, color: highPressure.length > 0 ? theme.red : theme.statusGreen },
        { label: "Max Heap%", value: `${Math.max(...data.nodes.map(n => normPct(n.max_heap_pct)))}%`, color: theme.orange },
        { label: "Max CPU%", value: `${Math.max(...data.nodes.map(n => normPct(n.max_cpu_pct)))}%` },
      ]} />

      <SectionCard title="Pressure Score Ranking">
        <HBarChart items={pressureItems} />
      </SectionCard>

      {data.nodes.slice(0, 5).map((n, i) => (
        <SectionCard key={i} title={`${n.node_name} — ${n.cluster_name}`} collapsible>
          <div style={{ display: "flex", gap: 12, justifyContent: "flex-start", flexWrap: "wrap", marginBottom: 8 }}>
            {n.max_heap_pct != null && <Gauge value={normPct(n.max_heap_pct)} label="Heap Max" size={80} />}
            {n.max_cpu_pct != null && <Gauge value={normPct(n.max_cpu_pct)} label="CPU Max" size={80}
              thresholds={[{ at: 80, color: theme.orange }, { at: 95, color: theme.red }]} />}
            {n.disk_used_pct != null && <Gauge value={normPct(n.disk_used_pct)} label="Disk Used" size={80}
              thresholds={[{ at: 70, color: theme.orange }, { at: 85, color: theme.red }]} />}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
            {(n.write_rejected ?? 0) > 0 && <span style={{ fontSize: 11, color: theme.red, background: `${theme.red}18`, padding: "2px 8px", borderRadius: 4 }}>write rejected: {n.write_rejected}</span>}
            {(n.search_rejected ?? 0) > 0 && <span style={{ fontSize: 11, color: theme.orange, background: `${theme.orange}18`, padding: "2px 8px", borderRadius: 4 }}>search rejected: {n.search_rejected}</span>}
            {n.segments_count != null && <span style={{ fontSize: 11, color: theme.textMuted, background: theme.bgTertiary, padding: "2px 8px", borderRadius: 4 }}>{n.segments_count} segments</span>}
          </div>
        </SectionCard>
      ))}
      <InvestigationActions actions={invActions} onSend={onSend} />
    </>
  );
}

function GcPressureView({ data, onSend }: { data: { lookback: string; nodes: GcEntry[] }; onSend: (p: string) => void }) {
  if (!data.nodes.length) return <div style={{ color: theme.textMuted }}>No GC data.</div>;

  // Histogram: old GC ms distribution
  const gcBuckets: HistogramBucket[] = data.nodes.map(n => ({
    label: n.node_name.split("-").slice(-1)[0] || n.node_name,
    value: n.old_gc_ms ?? 0,
    color: (n.old_gc_ms ?? 0) > 5000 ? theme.red : (n.old_gc_ms ?? 0) > 1000 ? theme.orange : theme.teal,
  }));

  const invActions: InvAction[] = [
    { label: "Node pressure details", prompt: `Run node_pressure_summary for the last ${data.lookback}`, icon: "📊" },
    { label: "Thread pool rejections", prompt: `Run thread_pool_rejections for the last ${data.lookback}`, icon: "🚫" },
  ];

  return (
    <>
      <StatRow stats={[
        { label: "Nodes Checked", value: data.nodes.length },
        { label: "Max Old GC ms", value: `${Math.max(...data.nodes.map(n => n.old_gc_ms ?? 0)).toLocaleString()}`, color: theme.orange },
        { label: "Max Heap%", value: `${Math.max(...data.nodes.map(n => normPct(n.max_heap_pct)))}%`, color: theme.red },
      ]} />

      <SectionCard title="Old GC Time by Node (ms)">
        <Histogram buckets={gcBuckets} height={120} yUnit="ms"
          colorFn={(v, max) => v > max * 0.7 ? theme.red : v > max * 0.4 ? theme.orange : theme.teal} />
      </SectionCard>

      <SectionCard title="GC Details" collapsible>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: theme.textMuted, fontSize: 10, textTransform: "uppercase" }}>
              <th style={{ textAlign: "left", padding: "4px 8px 8px 0" }}>Node</th>
              <th style={{ textAlign: "right", padding: "4px 8px 8px 0" }}>Heap%</th>
              <th style={{ textAlign: "right", padding: "4px 8px 8px 0" }}>Young ms</th>
              <th style={{ textAlign: "right", padding: "4px 8px 8px 0" }}>Old ms</th>
              <th style={{ textAlign: "right", padding: "4px 0 8px 0" }}>Score</th>
            </tr>
          </thead>
          <tbody>
            {data.nodes.map((n, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                <td style={{ padding: "5px 8px 5px 0", color: theme.text }} className="mono">{n.node_name}</td>
                <td style={{ padding: "5px 8px 5px 0", textAlign: "right", color: normPct(n.max_heap_pct) >= 90 ? theme.red : normPct(n.max_heap_pct) >= 75 ? theme.orange : theme.text }} className="mono">{normPct(n.max_heap_pct)}%</td>
                <td style={{ padding: "5px 8px 5px 0", textAlign: "right", color: theme.textMuted }} className="mono">{n.young_gc_ms?.toLocaleString() ?? "—"}</td>
                <td style={{ padding: "5px 8px 5px 0", textAlign: "right", color: (n.old_gc_ms ?? 0) > 1000 ? theme.red : theme.textMuted }} className="mono">{n.old_gc_ms?.toLocaleString() ?? "—"}</td>
                <td style={{ padding: "5px 0", textAlign: "right", color: theme.textFaint }} className="mono">{n.gc_pressure_score?.toFixed(0) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SectionCard>
      <InvestigationActions actions={invActions} onSend={onSend} />
    </>
  );
}

function ClusterTimelineView({ data }: { data: { lookback: string; clusters: ClusterTimeline[] } }) {
  if (!data.clusters.length) return <div style={{ color: theme.textMuted }}>No timeline data.</div>;

  return (
    <>
      {data.clusters.map((c) => {
        const heapSeries: import("@shared/charts").ChartSeries[] = [{
          label: "Heap %",
          color: theme.teal,
          points: c.points.map(p => ({ ts: p.ts, value: normPct(p.heap_pct) })),
          filled: true,
        }];
        const cells: HeatCell[] = c.points.map(p => ({ ts: p.ts, status: p.status }));
        const maxHeap = Math.max(...c.points.map(p => normPct(p.heap_pct)), 0);

        return (
          <SectionCard key={c.cluster_name} title={c.cluster_name}>
            <div style={{ fontSize: 10, color: theme.textMuted, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 }}>Status History</div>
            <StatusHeatmap cells={cells} height={28} />
            <div style={{ fontSize: 10, color: theme.textMuted, marginTop: 12, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Heap % Trend (max {maxHeap}%)
            </div>
            <AreaChart
              series={heapSeries}
              yMax={100}
              yUnit="%"
              height={130}
              thresholds={[
                { value: 75, color: theme.orange, label: "75%" },
                { value: 90, color: theme.red, label: "90%" },
              ]}
            />
          </SectionCard>
        );
      })}
    </>
  );
}

function NodeMetricsTimelineView({ data }: { data: { lookback: string; clusters: ClusterNodeTimeline[] } }) {
  if (!data.clusters.length) return <div style={{ color: theme.textMuted }}>No node timeline data.</div>;

  return (
    <>
      {data.clusters.map((c) => {
        // Build multi-series for heap
        const heapSeries: import("@shared/charts").ChartSeries[] = c.nodes.slice(0, 6).map((n, i) => ({
          label: n.node_name,
          color: NODE_COLORS[i % NODE_COLORS.length],
          points: n.points.map(p => ({ ts: p.ts, value: normPct(p.heap_pct) })),
          filled: false,
        }));

        return (
          <SectionCard key={c.cluster_name} title={`${c.cluster_name} — Node Heap % Trend`}>
            <AreaChart series={heapSeries} yMax={100} yUnit="%" height={150}
              thresholds={[{ value: 85, color: theme.red, label: "85%" }]} />
            <div style={{ marginTop: 12 }}>
              {c.nodes.slice(0, 8).map((n, i) => {
                const heapValues = n.points.map(p => normPct(p.heap_pct));
                const cpuValues  = n.points.map(p => normPct(p.cpu_pct));
                const lastHeap = heapValues[heapValues.length - 1] ?? 0;
                const lastCpu  = cpuValues[cpuValues.length - 1] ?? 0;
                return (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: `1px solid ${theme.border}` }}>
                    <span className="mono" style={{ fontSize: 11, color: NODE_COLORS[i % NODE_COLORS.length], width: 140, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{n.node_name}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span style={{ fontSize: 9, color: theme.textMuted }}>heap</span>
                      <Sparkline points={heapValues} color={NODE_COLORS[i % NODE_COLORS.length]} width={56} height={18} />
                      <span className="mono" style={{ fontSize: 10, color: lastHeap >= 90 ? theme.red : lastHeap >= 75 ? theme.orange : theme.text, width: 34 }}>{lastHeap}%</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span style={{ fontSize: 9, color: theme.textMuted }}>cpu</span>
                      <Sparkline points={cpuValues} color={theme.purple} width={56} height={18} />
                      <span className="mono" style={{ fontSize: 10, color: lastCpu >= 90 ? theme.red : theme.textMuted, width: 34 }}>{lastCpu}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>
        );
      })}
    </>
  );
}

// ── Root App ─────────────────────────────────────────────────────────────────

export function App() {
  const appRef = useRef<AppLike | null>(null);
  const [payload, setPayload] = useState<Payload | null>(null);

  useApp({
    appInfo: { name: "elastic-cluster-triage-agent", version: "1.0.0" },
    onAppCreated: (app) => {
      appRef.current = app;
      app.ontoolresult = (params: ToolResultParams) => {
        const data = parseToolResult<Payload>(params);
        if (data) setPayload(data);
      };
    },
  });

  const sendMessage = (prompt: string) => appRef.current?.sendMessage?.(prompt);

  if (!payload) {
    return (
      <div className="ds-view" style={{ color: theme.textMuted, fontSize: 12, padding: 16 }}>
        Waiting for cluster health data…
      </div>
    );
  }

  const title = payload.tool.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="ds-view">
      <ViewHeader title={title} subtitle={`elasticsearch cluster health · ${payload.lookback}`} />

      {payload.tool === "cluster_health_summary" && <ClusterHealthView data={payload} onSend={sendMessage} />}
      {payload.tool === "red_yellow_periods" && <RedYellowView data={payload} onSend={sendMessage} />}
      {payload.tool === "node_last_seen" && <NodeLastSeenView data={payload} />}
      {payload.tool === "node_pressure_summary" && <NodePressureView data={payload} onSend={sendMessage} />}
      {payload.tool === "jvm_gc_pressure" && <GcPressureView data={payload} onSend={sendMessage} />}
      {payload.tool === "cluster_timeline" && <ClusterTimelineView data={payload} />}
      {payload.tool === "node_metrics_timeline" && <NodeMetricsTimelineView data={payload} />}
    </div>
  );
}
