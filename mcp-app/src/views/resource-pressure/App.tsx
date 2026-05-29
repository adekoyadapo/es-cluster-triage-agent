import React, { useRef, useState } from "react";
import { useApp, AppLike, ToolResultParams } from "@shared/use-app";
import { parseToolResult } from "@shared/parse-tool-result";
import { theme } from "@shared/theme";
import {
  Gauge,
  HBarChart, HBarItem,
  Histogram, HistogramBucket,
  ViewHeader, StatRow, SectionCard,
  InvestigationActions, InvAction,
} from "@shared/charts";

interface ClusterDisk {
  cluster_name: string; status: string;
  disk_used_pct: number | null; disk_avail_gb: number | null; disk_total_gb: number | null;
  node_count: number | null; shards: number | null;
}
interface NodeRejections {
  cluster_name: string; node_name: string;
  write_rejected: number; search_rejected: number; bulk_rejected: number;
  write_queue: number; search_queue: number; bulk_queue: number; rejection_score: number;
}
interface NodeFailure {
  cluster_name: string; node_name: string;
  write_rejected: number; bulk_rejected: number;
  coordinating_rejections: number; primary_rejections: number; replica_rejections: number;
  indexing_pressure_mb: number | null; failure_score: number;
}

type Payload =
  | { tool: "disk_shard_pressure"; lookback: string; clusters: ClusterDisk[] }
  | { tool: "thread_pool_rejections"; lookback: string; nodes: NodeRejections[] }
  | { tool: "indexing_failure_analysis"; lookback: string; nodes: NodeFailure[] };

function statusColor(s: string) {
  if (s === "green") return theme.statusGreen;
  if (s === "yellow") return theme.statusYellow;
  if (s === "red") return theme.statusRed;
  return theme.textMuted;
}

function normPct(v: number | null): number {
  if (v == null) return 0;
  return v > 100 ? Math.min(v / 100, 100) : v;
}

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

  if (!payload) return <div className="ds-view" style={{ color: theme.textMuted, fontSize: 12 }}>Waiting for resource pressure data…</div>;

  const title = payload.tool.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="ds-view">
      <ViewHeader title={title} subtitle={`resource pressure · ${payload.lookback}`} />

      {payload.tool === "disk_shard_pressure" && (() => {
        if (!payload.clusters.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No disk pressure detected.</div>;

        const maxDiskPct = Math.max(...payload.clusters.map(c => normPct(c.disk_used_pct)));
        const pressured = payload.clusters.filter(c => (c.disk_used_pct ?? 0) >= 70).length;

        const diskItems: HBarItem[] = payload.clusters.map(c => ({
          label: c.cluster_name,
          value: c.disk_used_pct ?? 0,
          sub: `${c.disk_avail_gb}GB free`,
          color: (c.disk_used_pct ?? 0) >= 85 ? theme.red : (c.disk_used_pct ?? 0) >= 70 ? theme.orange : theme.teal,
        }));

        const invActions: InvAction[] = [
          { label: "ILM stuck indices", prompt: `Run ilm_stuck_indices for the last ${payload.lookback}`, icon: "🔄" },
          { label: "Index growth", prompt: `Run index_growth_analysis for the last ${payload.lookback}`, icon: "📈" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Clusters", value: payload.clusters.length },
              { label: "Disk Pressured", value: pressured, color: pressured > 0 ? theme.red : theme.statusGreen },
              { label: "Max Disk%", value: `${maxDiskPct}%`, color: maxDiskPct >= 85 ? theme.red : maxDiskPct >= 70 ? theme.orange : theme.statusGreen },
            ]} />
            <SectionCard title="Disk Usage by Cluster">
              <HBarChart items={diskItems} max={100} unit="%" />
            </SectionCard>
            {payload.clusters.map((c, i) => (
              <SectionCard key={i} title={c.cluster_name} collapsible>
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  {c.disk_used_pct != null && (
                    <Gauge value={normPct(c.disk_used_pct)} label="Disk Used" size={90}
                      thresholds={[{ at: 70, color: theme.orange }, { at: 85, color: theme.red }]} />
                  )}
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <span style={{
                        padding: "2px 10px", borderRadius: 99, fontSize: 11, fontWeight: 700,
                        background: `${statusColor(c.status)}18`, color: statusColor(c.status),
                        border: `1px solid ${statusColor(c.status)}55`,
                      }}>{c.status.toUpperCase()}</span>
                    </div>
                    <div style={{ fontSize: 11, color: theme.textMuted }}>{c.node_count} nodes · {c.shards} shards</div>
                    <div style={{ fontSize: 11, color: theme.textMuted }}>{c.disk_avail_gb}GB free / {c.disk_total_gb}GB total</div>
                  </div>
                </div>
              </SectionCard>
            ))}
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "thread_pool_rejections" && (() => {
        const active = payload.nodes.filter(n => n.rejection_score > 0);
        if (!active.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No thread pool rejections detected.</div>;

        const totalWrite = active.reduce((s, n) => s + n.write_rejected, 0);
        const totalSearch = active.reduce((s, n) => s + n.search_rejected, 0);
        const totalBulk = active.reduce((s, n) => s + n.bulk_rejected, 0);

        const poolBuckets: HistogramBucket[] = [
          { label: "write", value: totalWrite, color: theme.red },
          { label: "bulk", value: totalBulk, color: theme.orange },
          { label: "search", value: totalSearch, color: theme.yellow },
        ].filter(b => b.value > 0);

        const scoreItems: HBarItem[] = active.map(n => ({
          label: n.node_name,
          value: n.rejection_score,
          color: n.rejection_score > 10000 ? theme.red : n.rejection_score > 1000 ? theme.orange : theme.yellow,
        }));

        const invActions: InvAction[] = [
          { label: "Check indexing failures", prompt: `Run indexing_failure_analysis for the last ${payload.lookback}`, icon: "🔥" },
          { label: "Disk pressure", prompt: `Run disk_shard_pressure for the last ${payload.lookback}`, icon: "💾" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Nodes w/ Rejections", value: active.length, color: theme.red },
              { label: "Write Rejected", value: totalWrite.toLocaleString(), color: theme.red },
              { label: "Bulk Rejected", value: totalBulk.toLocaleString(), color: theme.orange },
              { label: "Search Rejected", value: totalSearch.toLocaleString(), color: theme.yellow },
            ]} />
            {poolBuckets.length > 0 && (
              <SectionCard title="Rejections by Pool Type">
                <Histogram buckets={poolBuckets} height={100} />
              </SectionCard>
            )}
            <SectionCard title="Rejection Score by Node">
              <HBarChart items={scoreItems} />
            </SectionCard>
            <SectionCard title="Queue Details" collapsible>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: theme.textMuted, fontSize: 10, textTransform: "uppercase" }}>
                    <th style={{ textAlign: "left", padding: "4px 8px 8px 0" }}>Node</th>
                    <th style={{ textAlign: "right", padding: "4px 6px 8px" }}>Write Rej</th>
                    <th style={{ textAlign: "right", padding: "4px 6px 8px" }}>Bulk Rej</th>
                    <th style={{ textAlign: "right", padding: "4px 6px 8px" }}>Search Rej</th>
                    <th style={{ textAlign: "right", padding: "4px 0 8px" }}>Queues</th>
                  </tr>
                </thead>
                <tbody>
                  {active.map((n, i) => (
                    <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", color: theme.text }}>{n.node_name}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 6px", color: n.write_rejected > 0 ? theme.red : theme.textFaint }}>{n.write_rejected || "—"}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 6px", color: n.bulk_rejected > 0 ? theme.red : theme.textFaint }}>{n.bulk_rejected || "—"}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 6px", color: n.search_rejected > 0 ? theme.orange : theme.textFaint }}>{n.search_rejected || "—"}</td>
                      <td style={{ textAlign: "right", padding: "4px 0", fontSize: 10, color: theme.textFaint }}>w:{n.write_queue} s:{n.search_queue}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "indexing_failure_analysis" && (() => {
        const active = payload.nodes.filter(n => n.failure_score > 0);
        if (!active.length) return <div style={{ color: theme.statusGreen, padding: 12 }}>✓ No indexing failures detected.</div>;

        const pressureItems: HBarItem[] = active.map(n => ({
          label: n.node_name,
          value: n.failure_score,
          color: n.failure_score > 5000 ? theme.red : theme.orange,
        }));

        const invActions: InvAction[] = [
          { label: "Thread pool rejections", prompt: `Run thread_pool_rejections for the last ${payload.lookback}`, icon: "🚫" },
          { label: "Index pressure", prompt: `Run index_pressure_analysis for the last ${payload.lookback}`, icon: "📊" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Nodes Affected", value: active.length, color: theme.red },
              { label: "Total Write Rej", value: active.reduce((s, n) => s + n.write_rejected, 0).toLocaleString(), color: theme.red },
              { label: "Coordinating Rej", value: active.reduce((s, n) => s + n.coordinating_rejections, 0).toLocaleString(), color: theme.orange },
            ]} />
            <SectionCard title="Failure Pressure by Node">
              <HBarChart items={pressureItems} />
            </SectionCard>
            <SectionCard title="Failure Details" collapsible>
              {active.map((n, i) => (
                <div key={i} style={{ borderTop: i > 0 ? `1px solid ${theme.border}` : undefined, paddingTop: i > 0 ? 8 : 0, marginTop: i > 0 ? 8 : 0 }}>
                  <div style={{ fontSize: 12, color: theme.text, marginBottom: 4 }} className="mono">{n.node_name}</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {n.coordinating_rejections > 0 && <span style={{ fontSize: 11, color: theme.red, background: `${theme.red}18`, padding: "2px 8px", borderRadius: 4 }}>coordinating: {n.coordinating_rejections}</span>}
                    {n.primary_rejections > 0 && <span style={{ fontSize: 11, color: theme.red, background: `${theme.red}18`, padding: "2px 8px", borderRadius: 4 }}>primary: {n.primary_rejections}</span>}
                    {n.replica_rejections > 0 && <span style={{ fontSize: 11, color: theme.orange, background: `${theme.orange}18`, padding: "2px 8px", borderRadius: 4 }}>replica: {n.replica_rejections}</span>}
                    {n.indexing_pressure_mb != null && <span style={{ fontSize: 11, color: theme.textMuted }}>pressure: {n.indexing_pressure_mb}MB</span>}
                  </div>
                </div>
              ))}
            </SectionCard>
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}
    </div>
  );
}
