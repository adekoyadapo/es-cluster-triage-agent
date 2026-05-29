import { esRequest } from "./client.js";
import type { EsqlResult } from "../shared/types.js";

/**
 * Substitute named ES|QL parameters (?name) with their string values.
 * Values used in string comparisons are wrapped in double-quotes; time
 * duration values used inside function calls like TO_TIMEDURATION() are
 * substituted as bare quoted strings that ES|QL interprets as literals.
 */
export function buildQuery(
  template: string,
  params: Record<string, string>
): string {
  let query = template;
  for (const [key, value] of Object.entries(params)) {
    // Escape any double-quotes in the value to prevent injection
    const safe = value.replace(/"/g, '\\"');
    query = query.replaceAll(`?${key}`, `"${safe}"`);
  }
  return query;
}

export async function executeEsql(query: string): Promise<EsqlResult> {
  return esRequest<EsqlResult>("/_query", {
    body: { query },
    params: { format: "json" },
  });
}

export async function safeEsqlRows<T>(
  query: string,
  errors?: string[]
): Promise<T[]> {
  try {
    const res = await executeEsql(query);
    return res.values.map((row) => {
      const obj: Record<string, unknown> = {};
      res.columns.forEach((col, i) => {
        obj[col.name] = row[i];
      });
      return obj as T;
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    process.stderr.write(`[elastic-cluster-triage] ES|QL failed: ${msg}\n`);
    if (errors) errors.push(msg);
    return [];
  }
}

export function formatResults(result: { columns: { name: string; type: string }[]; values: unknown[][] }): string {
  if (!result.values.length) return "No data returned for this time window.";
  const header = result.columns.map((c) => c.name).join("\t");
  const rows = result.values
    .map((row) => row.map((v) => (v === null ? "null" : String(v))).join("\t"))
    .join("\n");
  return `${header}\n${rows}`;
}
