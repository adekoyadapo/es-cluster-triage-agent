import type { ElasticConfig } from "../shared/types.js";

let _config: ElasticConfig | null = null;

// When an optional user_config field in manifest.json is left blank,
// Claude Desktop passes the literal un-substituted placeholder string
// (e.g. `${user_config.kibana_api_key}`). Treat those as unset.
function cleanEnv(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (value.includes("${user_config.")) return undefined;
  return value;
}

export function isKibanaConfigured(): boolean {
  return Boolean(cleanEnv(process.env.KIBANA_URL));
}

export function getConfig(): ElasticConfig {
  if (!_config) {
    const elasticsearchUrl = cleanEnv(process.env.ELASTICSEARCH_URL);
    const elasticsearchApiKey = cleanEnv(process.env.ELASTICSEARCH_API_KEY);
    const kibanaUrl = cleanEnv(process.env.KIBANA_URL);
    const kibanaApiKey = cleanEnv(process.env.KIBANA_API_KEY);

    if (!elasticsearchUrl || !elasticsearchApiKey) {
      throw new Error(
        "ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY environment variables are required"
      );
    }

    _config = {
      elasticsearchUrl: elasticsearchUrl.replace(/\/$/, ""),
      elasticsearchApiKey,
      kibanaUrl: (kibanaUrl || elasticsearchUrl).replace(/\/$/, ""),
      kibanaApiKey: kibanaApiKey || elasticsearchApiKey,
    };
  }
  return _config;
}

export async function esRequest<T = unknown>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    params?: Record<string, string>;
  } = {}
): Promise<T> {
  const config = getConfig();
  const url = new URL(path, config.elasticsearchUrl);
  if (options.params) {
    for (const [k, v] of Object.entries(options.params)) {
      url.searchParams.set(k, v);
    }
  }

  const res = await fetch(url.toString(), {
    method: options.method || (options.body ? "POST" : "GET"),
    headers: {
      Authorization: `ApiKey ${config.elasticsearchApiKey}`,
      "Content-Type": "application/json",
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    signal: AbortSignal.timeout(30_000),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Elasticsearch ${res.status}: ${text}`);
  }

  return res.json() as Promise<T>;
}

export async function kibanaRequest<T = unknown>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    params?: Record<string, string>;
  } = {}
): Promise<T> {
  const config = getConfig();
  const url = new URL(config.kibanaUrl + path);
  if (options.params) {
    for (const [k, v] of Object.entries(options.params)) {
      url.searchParams.set(k, v);
    }
  }

  const res = await fetch(url.toString(), {
    method: options.method || (options.body ? "POST" : "GET"),
    headers: {
      Authorization: `ApiKey ${config.kibanaApiKey}`,
      "Content-Type": "application/json",
      "kbn-xsrf": "true",
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    signal: AbortSignal.timeout(30_000),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Kibana ${res.status}: ${text}`);
  }

  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}
