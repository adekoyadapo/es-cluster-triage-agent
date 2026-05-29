import React, { useRef, useState } from "react";
import { useApp, AppLike, ToolResultParams } from "@shared/use-app";
import { parseToolResult } from "@shared/parse-tool-result";
import { theme } from "@shared/theme";
import {
  HBarChart, HBarItem,
  Histogram, HistogramBucket,
  ViewHeader, StatRow, SectionCard,
  InvestigationActions, InvAction,
} from "@shared/charts";

interface LogEntry { bucket: string; level: string; host: string | null; message: string | null; events: number; }
interface AuditEntry { bucket: string; action: string | null; user: string | null; source_ip: string | null; count: number; }

type Payload =
  | { tool: "error_log_summary"; lookback: string; logs: LogEntry[]; summary: { error_count: number; warn_count: number } }
  | { tool: "audit_security_events"; lookback: string; events: AuditEntry[]; summary: { total_failures: number } };

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

  if (!payload) return <div className="ds-view" style={{ color: theme.textMuted, fontSize: 12 }}>Waiting for log data…</div>;

  const title = payload.tool.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="ds-view">
      <ViewHeader title={title} subtitle={`logs & security · ${payload.lookback}`} />

      {payload.tool === "error_log_summary" && (() => {
        const errors = payload.logs.filter(l => l.level === "ERROR");
        const warns = payload.logs.filter(l => l.level === "WARN");

        // Histogram: error/warn counts over time buckets
        const timeBuckets: HistogramBucket[] = [];
        const bucketMap: Record<string, { errors: number; warns: number }> = {};
        for (const l of payload.logs) {
          const key = l.bucket;
          if (!bucketMap[key]) bucketMap[key] = { errors: 0, warns: 0 };
          if (l.level === "ERROR") bucketMap[key].errors += l.events;
          else bucketMap[key].warns += l.events;
        }
        Object.entries(bucketMap)
          .sort((a, b) => a[0].localeCompare(b[0]))
          .forEach(([bucket, counts]) => {
            timeBuckets.push({
              label: new Date(bucket).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
              value: counts.errors + counts.warns,
              color: counts.errors > counts.warns ? theme.red : theme.orange,
            });
          });

        // HBarChart: top hosts by error count
        const hostMap: Record<string, number> = {};
        for (const l of payload.logs) {
          if (l.host) hostMap[l.host] = (hostMap[l.host] ?? 0) + l.events;
        }
        const hostItems: HBarItem[] = Object.entries(hostMap)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 10)
          .map(([host, count]) => ({
            label: host,
            value: count,
            color: count > 100 ? theme.red : count > 20 ? theme.orange : theme.yellow,
          }));

        const invActions: InvAction[] = [
          { label: "Audit security events", prompt: `Run audit_security_events for the last ${payload.lookback}`, icon: "🔒" },
          { label: "Cluster health", prompt: `Run cluster_health_summary for the last ${payload.lookback}`, icon: "🏥" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Errors", value: payload.summary.error_count.toLocaleString(), color: theme.red },
              { label: "Warnings", value: payload.summary.warn_count.toLocaleString(), color: theme.orange },
              { label: "Hosts Affected", value: Object.keys(hostMap).length },
              { label: "Log Groups", value: payload.logs.length },
            ]} />

            {payload.logs.length === 0 ? (
              <div style={{ color: theme.statusGreen, padding: 12, fontSize: 12, fontWeight: 600 }}>✓ No errors or warnings in this window.</div>
            ) : (
              <>
                {timeBuckets.length > 1 && (
                  <SectionCard title="Error/Warning Frequency Over Time">
                    <Histogram buckets={timeBuckets} height={110}
                      colorFn={(v, max) => v > max * 0.6 ? theme.red : v > max * 0.3 ? theme.orange : theme.yellow} />
                  </SectionCard>
                )}
                {hostItems.length > 0 && (
                  <SectionCard title="Top Hosts by Event Count">
                    <HBarChart items={hostItems} />
                  </SectionCard>
                )}
                <SectionCard title={`Log Events (${payload.logs.length})`} collapsible>
                  {payload.logs.slice(0, 25).map((log, i) => (
                    <div key={i} style={{ padding: "5px 0", borderTop: i > 0 ? `1px solid ${theme.border}` : undefined }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: log.level === "ERROR" ? theme.red : theme.orange, width: 36, flexShrink: 0 }}>{log.level}</span>
                        <span className="mono" style={{ fontSize: 10, color: theme.textMuted }}>{log.host ?? "—"}</span>
                        <span style={{ marginLeft: "auto", fontSize: 10, color: theme.textFaint, flexShrink: 0 }}>{new Date(log.bucket).toLocaleTimeString()} · {log.events}×</span>
                      </div>
                      {log.message && <div style={{ fontSize: 11, color: theme.text, paddingLeft: 42, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{log.message}</div>}
                    </div>
                  ))}
                </SectionCard>
              </>
            )}
            <InvestigationActions actions={invActions} onSend={sendMessage} />
          </>
        );
      })()}

      {payload.tool === "audit_security_events" && (() => {
        if (!payload.events.length) return (
          <div style={{ color: theme.statusGreen, padding: 12, fontSize: 12, fontWeight: 600 }}>✓ No auth failures in this window.</div>
        );

        // HBarChart: top source IPs by failure count
        const ipMap: Record<string, number> = {};
        for (const e of payload.events) {
          if (e.source_ip) ipMap[e.source_ip] = (ipMap[e.source_ip] ?? 0) + e.count;
        }
        const ipItems: HBarItem[] = Object.entries(ipMap)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 10)
          .map(([ip, count]) => ({
            label: ip,
            value: count,
            color: count > 50 ? theme.red : count > 10 ? theme.orange : theme.yellow,
          }));

        // HBarChart: top users
        const userMap: Record<string, number> = {};
        for (const e of payload.events) {
          const u = e.user ?? "unknown";
          userMap[u] = (userMap[u] ?? 0) + e.count;
        }
        const userItems: HBarItem[] = Object.entries(userMap)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 8)
          .map(([user, count]) => ({
            label: user,
            value: count,
            color: count > 50 ? theme.red : theme.orange,
          }));

        // Histogram: failures by action type
        const actionMap: Record<string, number> = {};
        for (const e of payload.events) {
          const a = e.action ?? "unknown";
          actionMap[a] = (actionMap[a] ?? 0) + e.count;
        }
        const actionBuckets: HistogramBucket[] = Object.entries(actionMap)
          .sort((a, b) => b[1] - a[1])
          .map(([action, count]) => ({
            label: action.split("_").slice(-1)[0],
            value: count,
            color: theme.red,
          }));

        const invActions: InvAction[] = [
          { label: "Error logs", prompt: `Run error_log_summary for the last ${payload.lookback}`, icon: "📋" },
          { label: "Cluster health", prompt: `Run cluster_health_summary for the last ${payload.lookback}`, icon: "🏥" },
        ];

        return (
          <>
            <StatRow stats={[
              { label: "Total Failures", value: payload.summary.total_failures.toLocaleString(), color: theme.red },
              { label: "Unique IPs", value: Object.keys(ipMap).length, color: theme.orange },
              { label: "Unique Users", value: Object.keys(userMap).length },
              { label: "Event Types", value: payload.events.length },
            ]} />
            {actionBuckets.length > 0 && (
              <SectionCard title="Failures by Action Type">
                <Histogram buckets={actionBuckets} height={100} />
              </SectionCard>
            )}
            {ipItems.length > 0 && (
              <SectionCard title="Top Source IPs">
                <HBarChart items={ipItems} />
              </SectionCard>
            )}
            {userItems.length > 0 && (
              <SectionCard title="Top Failing Users">
                <HBarChart items={userItems} />
              </SectionCard>
            )}
            <SectionCard title={`Security Events (${payload.events.length})`} collapsible>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: theme.textMuted, fontSize: 10, textTransform: "uppercase" }}>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>Action</th>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>User</th>
                    <th style={{ textAlign: "left", padding: "4px 8px 6px 0" }}>Source IP</th>
                    <th style={{ textAlign: "right", padding: "4px 0 6px 0" }}>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.events.slice(0, 25).map((e, i) => (
                    <tr key={i} style={{ borderTop: `1px solid ${theme.border}` }}>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", color: theme.orange }}>{e.action ?? "—"}</td>
                      <td style={{ padding: "4px 8px 4px 0", color: theme.text }}>{e.user ?? "—"}</td>
                      <td className="mono" style={{ padding: "4px 8px 4px 0", color: theme.textMuted }}>{e.source_ip ?? "—"}</td>
                      <td className="mono" style={{ textAlign: "right", padding: "4px 0", color: e.count > 10 ? theme.red : theme.textMuted }}>{e.count}</td>
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
