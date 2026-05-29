/**
 * Elastic Cluster Triage — SVG chart primitives
 * Pure SVG, zero external deps — safe for vite-plugin-singlefile bundles.
 * Design tokens from theme.ts; Kibana-inspired palette.
 */
import React, { useId } from "react";
import { theme } from "./theme.js";

// ── helpers ──────────────────────────────────────────────────────────────────

export function fmtTime(ts: string | number): string {
  const d = new Date(typeof ts === "number" ? ts : ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function fmtDate(ts: string | number): string {
  const d = new Date(typeof ts === "number" ? ts : ts);
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function toMs(ts: string | number) {
  return typeof ts === "number" ? ts : new Date(ts).getTime();
}

function niceMax(v: number, pct = false): number {
  if (pct) return 100;
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  return Math.ceil(v / mag) * mag;
}

// ── AreaChart / LineChart ─────────────────────────────────────────────────────

export interface ChartPoint { ts: string | number; value: number }
export interface ChartSeries {
  label: string;
  color: string;
  points: ChartPoint[];
  filled?: boolean;   // default true for area
  dashed?: boolean;
}

const PAD = { l: 48, r: 12, t: 10, b: 28 };

export function AreaChart({
  series,
  yMax: yMaxProp,
  yUnit = "",
  height = 140,
  thresholds,
}: {
  series: ChartSeries[];
  yMax?: number;
  yUnit?: string;
  height?: number;
  thresholds?: { value: number; color: string; label?: string }[];
}) {
  const id = useId().replace(/:/g, "");
  const W = 520;
  const H = height;
  const PW = W - PAD.l - PAD.r;
  const PH = H - PAD.t - PAD.b;

  const allPoints = series.flatMap((s) => s.points);
  if (allPoints.length < 2) {
    return <EmptyChart height={height} message="Insufficient data for trend" />;
  }

  const allTs = allPoints.map((p) => toMs(p.ts));
  const tMin = Math.min(...allTs);
  const tMax = Math.max(...allTs);
  const tRange = tMax - tMin || 1;

  const allVals = allPoints.map((p) => p.value);
  const rawMax = yMaxProp ?? Math.max(...allVals);
  const yMaxVal = niceMax(rawMax);
  const yTicks = Array.from({ length: 5 }, (_, i) => (yMaxVal * i) / 4);

  const xOf = (t: string | number) => PAD.l + ((toMs(t) - tMin) / tRange) * PW;
  const yOf = (v: number) => PAD.t + PH - (Math.min(v, yMaxVal) / yMaxVal) * PH;

  // x-axis tick labels (5 evenly spaced)
  const xTicks = Array.from({ length: 5 }, (_, i) => tMin + (tRange * i) / 4);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      preserveAspectRatio="xMidYMid meet"
      style={{ display: "block" }}
    >
      <defs>
        {series.map((s, si) =>
          s.filled !== false ? (
            <linearGradient key={si} id={`${id}g${si}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
            </linearGradient>
          ) : null
        )}
      </defs>

      {/* grid + y-axis */}
      {yTicks.map((t, i) => (
        <g key={i}>
          <line
            x1={PAD.l} x2={W - PAD.r}
            y1={yOf(t)} y2={yOf(t)}
            stroke={theme.border} strokeWidth={0.8}
            strokeDasharray={i === 0 ? undefined : "3 5"}
          />
          <text
            x={PAD.l - 5} y={yOf(t) + 3.5}
            textAnchor="end" fontSize={9}
            fontFamily="monospace" fill={theme.textMuted}
          >
            {t >= 1000 ? `${(t / 1000).toFixed(1)}k` : t.toFixed(t < 10 ? 1 : 0)}{yUnit}
          </text>
        </g>
      ))}

      {/* threshold lines */}
      {thresholds?.map((thr, i) => (
        <g key={i}>
          <line
            x1={PAD.l} x2={W - PAD.r}
            y1={yOf(thr.value)} y2={yOf(thr.value)}
            stroke={thr.color} strokeWidth={1} strokeDasharray="4 4" strokeOpacity={0.7}
          />
          {thr.label && (
            <text x={W - PAD.r - 2} y={yOf(thr.value) - 3} textAnchor="end" fontSize={8} fill={thr.color}>{thr.label}</text>
          )}
        </g>
      ))}

      {/* series */}
      {series.map((s, si) => {
        const sorted = [...s.points].sort((a, b) => toMs(a.ts) - toMs(b.ts));
        const line = sorted
          .map((p, i) => `${i === 0 ? "M" : "L"} ${xOf(p.ts).toFixed(1)} ${yOf(p.value).toFixed(1)}`)
          .join(" ");
        const area = line + ` L ${xOf(sorted[sorted.length - 1].ts).toFixed(1)} ${(PAD.t + PH).toFixed(1)} L ${PAD.l} ${(PAD.t + PH).toFixed(1)} Z`;

        return (
          <g key={si}>
            {s.filled !== false && (
              <path d={area} fill={`url(#${id}g${si})`} />
            )}
            <path
              d={line} fill="none"
              stroke={s.color} strokeWidth={2}
              strokeDasharray={s.dashed ? "5 4" : undefined}
              strokeLinejoin="round"
            />
          </g>
        );
      })}

      {/* x-axis labels */}
      {xTicks.map((t, i) => (
        <text
          key={i}
          x={xOf(t)} y={H - 6}
          textAnchor="middle" fontSize={9}
          fontFamily="monospace" fill={theme.textMuted}
        >
          {fmtTime(t)}
        </text>
      ))}

      {/* legend */}
      {series.length > 1 && series.map((s, si) => (
        <g key={si} transform={`translate(${PAD.l + si * 90}, ${H - PAD.b + 14})`}>
          <rect x={0} y={-5} width={12} height={4} rx={1} fill={s.color} />
          <text x={16} y={0} fontSize={9} fill={theme.textMuted}>{s.label}</text>
        </g>
      ))}
    </svg>
  );
}

// ── Histogram (vertical bars, time-bucketed) ──────────────────────────────────

export interface HistogramBucket { label: string; value: number; color?: string; sublabel?: string }

export function Histogram({
  buckets,
  height = 100,
  yUnit = "",
  colorFn,
}: {
  buckets: HistogramBucket[];
  height?: number;
  yUnit?: string;
  colorFn?: (v: number, max: number) => string;
}) {
  if (!buckets.length) return <EmptyChart height={height} message="No data" />;
  if (buckets.every(b => b.value === 0)) return <EmptyChart height={height} message="No data in this window" />;
  const maxVal = Math.max(...buckets.map((b) => b.value), 1);
  const W = 520;
  const H = height;
  const pad = { l: 36, r: 8, t: 8, b: 24 };
  const PW = W - pad.l - pad.r;
  const PH = H - pad.t - pad.b;
  const bw = PW / buckets.length;
  const gap = Math.max(1, bw * 0.15);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
      {/* y-axis labels */}
      {[0, maxVal / 2, maxVal].map((v, i) => (
        <text key={i} x={pad.l - 4} y={pad.t + PH - (v / maxVal) * PH + 3} textAnchor="end" fontSize={8} fontFamily="monospace" fill={theme.textMuted}>
          {v > 999 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)}{yUnit}
        </text>
      ))}

      {/* bars */}
      {buckets.map((b, i) => {
        const bh = (b.value / maxVal) * PH;
        const x = pad.l + i * bw + gap / 2;
        const y = pad.t + PH - bh;
        const color = b.color ?? (colorFn ? colorFn(b.value, maxVal) : theme.teal);
        return (
          <g key={i}>
            <rect x={x} y={y} width={bw - gap} height={Math.max(bh, 1)} rx={2} fill={color} fillOpacity={0.85} />
            {bw > 30 && (
              <text x={x + (bw - gap) / 2} y={H - 6} textAnchor="middle" fontSize={8} fontFamily="monospace" fill={theme.textMuted}>
                {b.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ── Gauge (arc) ───────────────────────────────────────────────────────────────

/**
 * Format a gauge value for display — keeps text short enough to fit.
 * e.g. 8700 → "8.7k", 87.5 → "88", 7 → "7.0"
 */
function fmtGaugeVal(v: number, unit: string): string {
  const abs = Math.abs(v);
  const num = abs >= 10000 ? `${(abs / 1000).toFixed(1)}k`
            : abs >= 1000  ? `${(Math.round(abs / 100) / 10).toFixed(1)}k`
            : abs >= 10    ? Math.round(abs).toString()
            : abs.toFixed(1);
  return `${num}${unit}`;
}

export function Gauge({
  value,
  max = 100,
  label,
  unit = "%",
  size = 100,
  thresholds = [{ at: 75, color: theme.orange }, { at: 90, color: theme.red }],
}: {
  value: number;
  max?: number;
  label?: string;
  unit?: string;
  size?: number;
  thresholds?: { at: number; color: string }[];
}) {
  const cx   = size / 2;
  const cy   = size * 0.52;          // moved up to give label room below
  const r    = size * 0.38;
  const thick = size * 0.10;
  const svgH  = size * 0.84;         // tall enough for label at cy + 0.22*size
  const startAngle = -200;
  const sweepDeg   = 220;

  const pct = Math.min(Math.max(value, 0), max) / max;

  // Threshold color — compare value against thresholds (thresholds use the same scale as value)
  const color = [...thresholds].reverse().find((t) => value >= t.at)?.color
    ?? (pct > 0.5 ? theme.teal : theme.greenSoft);

  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const startRad = toRad(startAngle);
  const endRad   = toRad(startAngle + sweepDeg);
  const fillRad  = toRad(startAngle + sweepDeg * pct);

  const arcPath = (radius: number, sa: number, ea: number, sweep: number) => {
    const x1 = cx + radius * Math.cos(sa);
    const y1 = cy + radius * Math.sin(sa);
    const x2 = cx + radius * Math.cos(ea);
    const y2 = cy + radius * Math.sin(ea);
    const large = sweep > Math.PI ? 1 : 0;
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  };

  const trackPath = arcPath(r, startRad, endRad, toRad(sweepDeg));
  const fillPath  = pct > 0.005 ? arcPath(r, startRad, fillRad, toRad(sweepDeg * pct)) : null;

  // Adaptive font: shrink when text is long so it stays inside the arc
  const displayText = fmtGaugeVal(value, unit);
  const charLen = displayText.length;
  const baseFontSz = size * 0.22;
  const fontSize = charLen > 5 ? baseFontSz * (5 / charLen) * 0.94
                 : charLen > 4 ? baseFontSz * 0.88
                 : baseFontSz;

  // Label y must sit below the arc opening but still inside svgH
  const labelY = Math.min(cy + size * 0.22, svgH - 3);

  return (
    <svg viewBox={`0 0 ${size} ${svgH}`} width={size} height={svgH} style={{ display: "block" }}>
      {/* track */}
      <path d={trackPath} fill="none" stroke={theme.bgTertiary}
            strokeWidth={thick} strokeLinecap="round" />
      {/* fill */}
      {fillPath && (
        <path d={fillPath} fill="none" stroke={color}
              strokeWidth={thick} strokeLinecap="round" />
      )}
      {/* value text — centred in arc interior */}
      <text
        x={cx} y={cy + fontSize * 0.38}
        textAnchor="middle"
        fontSize={fontSize} fontWeight={700}
        fontFamily="'Fira Code', monospace"
        fill={color}
      >
        {displayText}
      </text>
      {/* label */}
      {label && (
        <text
          x={cx} y={labelY}
          textAnchor="middle"
          fontSize={Math.max(size * 0.10, 9)}
          fontFamily="system-ui, sans-serif"
          fill={theme.textMuted}
        >
          {label}
        </text>
      )}
    </svg>
  );
}

// ── StatusHeatmap (time × status grid) ───────────────────────────────────────

export interface HeatCell { ts: string | number; status: string; label?: string }

export function StatusHeatmap({ cells, height = 36 }: { cells: HeatCell[]; height?: number }) {
  if (!cells.length) return <EmptyChart height={height} message="No status data" />;
  const W = 520;
  const cellW = Math.max(4, Math.min(24, W / cells.length));
  const gap = 2;

  function cellColor(s: string) {
    if (s === "green") return theme.statusGreen;
    if (s === "yellow") return theme.statusYellow;
    if (s === "red") return theme.statusRed;
    return theme.border;
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" preserveAspectRatio="none" style={{ display: "block", minWidth: 200 }}>
        {cells.map((c, i) => (
          <rect
            key={i}
            x={i * (cellW + gap)}
            y={0}
            width={cellW}
            height={height}
            rx={2}
            fill={cellColor(c.status)}
            fillOpacity={0.85}
          >
            <title>{fmtTime(c.ts)} — {c.status}</title>
          </rect>
        ))}
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: theme.textMuted, fontFamily: "monospace", marginTop: 2 }}>
        <span>{cells.length > 0 ? fmtTime(cells[0].ts) : ""}</span>
        <span>{cells.length > 0 ? fmtTime(cells[cells.length - 1].ts) : ""}</span>
      </div>
    </div>
  );
}

// ── Sparkline (inline mini trend) ────────────────────────────────────────────

export function Sparkline({ points, color = theme.teal, width = 64, height = 20 }: {
  points: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (points.length < 2) return <span className="mono" style={{ fontSize: 10, color }}>{points[0] ?? "—"}</span>;
  const max = Math.max(...points, 1);
  const min = Math.min(...points);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const xOf = (i: number) => i * step;
  const yOf = (v: number) => height - 2 - ((v - min) / range) * (height - 4);
  const path = points.map((v, i) => `${i === 0 ? "M" : "L"} ${xOf(i).toFixed(1)} ${yOf(v).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const prev = points[points.length - 2];
  const trend = last > prev ? "↑" : last < prev ? "↓" : "→";
  const trendColor = last > prev ? theme.red : last < prev ? theme.greenSoft : theme.textMuted;

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
        <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
        <circle cx={xOf(points.length - 1)} cy={yOf(last)} r={2} fill={color} />
      </svg>
      <span style={{ fontSize: 10, color: trendColor, fontWeight: 600 }}>{trend}</span>
    </span>
  );
}

// ── ScatterPlot ───────────────────────────────────────────────────────────────

export interface ScatterPoint { x: number; y: number; label: string; size?: number; color?: string }

export function ScatterPlot({
  points,
  xLabel = "X",
  yLabel = "Y",
  xUnit = "",
  yUnit = "",
  height = 180,
}: {
  points: ScatterPoint[];
  xLabel?: string;
  yLabel?: string;
  xUnit?: string;
  yUnit?: string;
  height?: number;
}) {
  if (!points.length) return <EmptyChart height={height} message="No data" />;
  if (points.every(p => p.x === 0 && p.y === 0)) return <EmptyChart height={height} message="All values are zero" />;
  const W = 520;
  const H = height;
  const pad = { l: 48, r: 16, t: 12, b: 32 };
  const PW = W - pad.l - pad.r;
  const PH = H - pad.t - pad.b;

  const xMax = Math.max(...points.map((p) => p.x), 1);
  const yMax = Math.max(...points.map((p) => p.y), 1);

  const xOf = (v: number) => pad.l + (v / xMax) * PW;
  const yOf = (v: number) => pad.t + PH - (v / yMax) * PH;

  const xTicks = Array.from({ length: 4 }, (_, i) => (xMax * (i + 1)) / 4);
  const yTicks = Array.from({ length: 4 }, (_, i) => (yMax * i) / 3);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
      {/* grid */}
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={pad.l} x2={W - pad.r} y1={yOf(t)} y2={yOf(t)} stroke={theme.border} strokeWidth={0.8} strokeDasharray="3 5" />
          <text x={pad.l - 5} y={yOf(t) + 3} textAnchor="end" fontSize={8} fontFamily="monospace" fill={theme.textMuted}>
            {t >= 1000 ? `${(t / 1000).toFixed(1)}k` : t.toFixed(0)}{yUnit}
          </text>
        </g>
      ))}
      {xTicks.map((t, i) => (
        <text key={i} x={xOf(t)} y={H - 6} textAnchor="middle" fontSize={8} fontFamily="monospace" fill={theme.textMuted}>
          {t >= 1000 ? `${(t / 1000).toFixed(1)}k` : t.toFixed(0)}{xUnit}
        </text>
      ))}
      {/* axis labels */}
      <text x={W / 2} y={H - 1} textAnchor="middle" fontSize={9} fill={theme.textMuted}>{xLabel}</text>
      <text x={10} y={H / 2} textAnchor="middle" fontSize={9} fill={theme.textMuted}
        transform={`rotate(-90, 10, ${H / 2})`}>{yLabel}</text>

      {/* points */}
      {points.map((p, i) => (
        <g key={i}>
          <circle
            cx={xOf(p.x)} cy={yOf(p.y)}
            r={p.size ?? 5}
            fill={p.color ?? theme.teal}
            fillOpacity={0.75}
            stroke={p.color ?? theme.teal}
            strokeOpacity={0.4}
            strokeWidth={1}
          />
          {(p.size ?? 5) > 4 && (
            <text
              x={xOf(p.x)} y={yOf(p.y) - 8}
              textAnchor="middle" fontSize={8}
              fill={theme.textMuted}
            >
              {p.label.length > 16 ? p.label.slice(-14) : p.label}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

// ── HBarChart (ranked horizontal bars) ───────────────────────────────────────

export interface HBarItem { label: string; value: number; sub?: string; color?: string }

export function HBarChart({ items, max: maxProp, unit = "", colorFn }: {
  items: HBarItem[];
  max?: number;
  unit?: string;
  colorFn?: (v: number, max: number) => string;
}) {
  const maxVal = maxProp ?? Math.max(...items.map((i) => i.value), 1);
  const barColor = (v: number) => colorFn ? colorFn(v, maxVal) : theme.teal;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {items.map((item, i) => {
        const pct = Math.min((item.value / maxVal) * 100, 100);
        const color = item.color ?? barColor(item.value);
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="mono" style={{ fontSize: 11, color: theme.text, width: 160, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={item.label}>
              {item.label.length > 22 ? "…" + item.label.slice(-20) : item.label}
            </span>
            <div style={{ flex: 1, height: 8, background: theme.bgTertiary, borderRadius: 4, overflow: "hidden", position: "relative" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 4, transition: "width .3s" }} />
            </div>
            <span className="mono" style={{ fontSize: 10, color, width: 54, textAlign: "right", flexShrink: 0 }}>
              {item.value >= 1e9 ? `${(item.value / 1e9).toFixed(1)}G` :
               item.value >= 1e6 ? `${(item.value / 1e6).toFixed(1)}M` :
               item.value >= 1e3 ? `${(item.value / 1e3).toFixed(1)}k` :
               item.value.toFixed(item.value < 10 ? 1 : 0)}{unit}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── InvestigationActions (drill-down prompt buttons) ─────────────────────────

export interface InvAction { label: string; prompt: string; icon?: string }

export function InvestigationActions({
  actions,
  onSend,
}: {
  actions: InvAction[];
  onSend: (prompt: string) => void;
}) {
  if (!actions.length) return null;
  return (
    <div style={{
      borderTop: `1px solid ${theme.border}`,
      paddingTop: 10,
      marginTop: 10,
      display: "flex",
      gap: 6,
      flexWrap: "wrap",
    }}>
      {actions.map((a, i) => (
        <button
          key={i}
          onClick={() => onSend(a.prompt)}
          style={{
            background: theme.bgTertiary,
            color: theme.text,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: "5px 11px",
            fontSize: 11,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            transition: "all 0.12s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = theme.border; e.currentTarget.style.borderColor = theme.borderStrong; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = theme.bgTertiary; e.currentTarget.style.borderColor = theme.border; }}
        >
          {a.icon && <span>{a.icon}</span>}
          <span>{a.label}</span>
          <span style={{ color: theme.textFaint, fontSize: 9 }}>↗</span>
        </button>
      ))}
    </div>
  );
}

// ── TimeRangeChips (re-invoke with different lookback) ────────────────────────

export function TimeRangeChips({
  current,
  onSelect,
  presets = ["15 minutes", "1 hour", "6 hours", "24 hours"],
}: {
  current: string;
  onSelect: (lb: string) => void;
  presets?: string[];
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <span style={{ fontSize: 9, color: theme.textMuted, textTransform: "uppercase", fontWeight: 600, letterSpacing: 0.5 }}>Range</span>
      {presets.map((p) => {
        const active = p === current;
        return (
          <button
            key={p}
            onClick={() => !active && onSelect(p)}
            className="mono"
            style={{
              background: active ? `${theme.teal}22` : theme.bgTertiary,
              color: active ? theme.teal : theme.textMuted,
              border: `1px solid ${active ? `${theme.teal}66` : theme.border}`,
              borderRadius: 4,
              padding: "3px 9px",
              fontSize: 10,
              fontWeight: active ? 700 : 500,
              cursor: active ? "default" : "pointer",
              transition: "all 0.1s",
            }}
          >
            {p.replace(" minutes", "m").replace(" hours", "h").replace(" hour", "h")}
          </button>
        );
      })}
    </div>
  );
}

// ── ViewHeader ────────────────────────────────────────────────────────────────

export function ViewHeader({
  title,
  subtitle,
  badge,
  lookback,
  onLookbackChange,
}: {
  title: string;
  subtitle?: string;
  badge?: { label: string; color: string };
  lookback?: string;
  onLookbackChange?: (lb: string) => void;
}) {
  return (
    <div style={{
      background: theme.bgSecondary, border: `1px solid ${theme.border}`,
      borderRadius: 8, padding: "12px 14px", marginBottom: 2,
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: theme.text }}>{title}</div>
          {subtitle && <div className="mono" style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>{subtitle}</div>}
        </div>
        {badge && (
          <span style={{
            background: `${badge.color}18`, color: badge.color,
            border: `1px solid ${badge.color}55`,
            borderRadius: 99, padding: "3px 10px", fontSize: 11, fontWeight: 700,
          }}>{badge.label}</span>
        )}
      </div>
      {lookback && onLookbackChange && (
        <div style={{ marginTop: 10 }}>
          <TimeRangeChips current={lookback} onSelect={onLookbackChange} />
        </div>
      )}
    </div>
  );
}

// ── StatRow (compact kpi row) ─────────────────────────────────────────────────

export function StatRow({ stats }: { stats: { label: string; value: React.ReactNode; color?: string }[] }) {
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {stats.map((s, i) => (
        <div key={i} style={{
          background: theme.bgSecondary, border: `1px solid ${theme.border}`,
          borderRadius: 6, padding: "8px 14px", flex: "1 1 0", minWidth: 80,
        }}>
          <div style={{ fontSize: 9, color: theme.textMuted, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 4 }}>{s.label}</div>
          <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: s.color ?? theme.text, lineHeight: 1.2 }}>{s.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Section card ─────────────────────────────────────────────────────────────

export function SectionCard({ title, children, collapsible }: {
  title: string;
  children: React.ReactNode;
  collapsible?: boolean;
}) {
  const [open, setOpen] = React.useState(true);
  return (
    <div style={{ background: theme.bgSecondary, border: `1px solid ${theme.border}`, borderRadius: 8, overflow: "hidden" }}>
      <div
        style={{ padding: "9px 14px", borderBottom: open ? `1px solid ${theme.border}` : "none", display: "flex", justifyContent: "space-between", alignItems: "center", cursor: collapsible ? "pointer" : "default" }}
        onClick={() => collapsible && setOpen((v) => !v)}
      >
        <span style={{ fontSize: 11, fontWeight: 600, color: theme.textMuted, textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</span>
        {collapsible && <span style={{ fontSize: 11, color: theme.textMuted }}>{open ? "▲" : "▼"}</span>}
      </div>
      {open && <div style={{ padding: "12px 14px" }}>{children}</div>}
    </div>
  );
}

// ── EmptyChart placeholder ────────────────────────────────────────────────────

function EmptyChart({ height, message }: { height: number; message: string }) {
  return (
    <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center",
      background: theme.bgTertiary, borderRadius: 6, border: `1px dashed ${theme.border}` }}>
      <span style={{ fontSize: 11, color: theme.textFaint }}>{message}</span>
    </div>
  );
}
