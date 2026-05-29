export interface EsqlColumn {
  name: string;
  type: string;
}

export interface EsqlResult {
  columns: EsqlColumn[];
  values: unknown[][];
}

export interface ElasticConfig {
  elasticsearchUrl: string;
  elasticsearchApiKey: string;
  kibanaUrl: string;
  kibanaApiKey: string;
}
