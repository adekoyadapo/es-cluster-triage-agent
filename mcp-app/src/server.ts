import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerClusterHealthTools } from "./tools/cluster-health.js";
import { registerResourcePressureTools } from "./tools/resource-pressure.js";
import { registerIndexPressureTools } from "./tools/index-pressure.js";
import { registerIndexLifecycleTools } from "./tools/index-lifecycle.js";
import { registerLogsSecurityTools } from "./tools/logs-security.js";
import { registerTimelineTools } from "./tools/timeline-tools.js";
import { registerAppIndexTools } from "./tools/app-index-tools.js";

// BUILD_MODE is injected at bundle time via esbuild --define.
// Values: "cluster" (default) | "app-index" | "combined"
declare const BUILD_MODE: string;
const mode = (typeof BUILD_MODE !== "undefined" ? BUILD_MODE : "cluster") as string;

export function createServer(): McpServer {
  const server = new McpServer({
    name: "elastic-cluster-triage-agent",
    version: "1.0.0",
  });

  if (mode !== "app-index") {
    registerClusterHealthTools(server);
    registerResourcePressureTools(server);
    registerIndexPressureTools(server);
    registerIndexLifecycleTools(server);
    registerLogsSecurityTools(server);
    registerTimelineTools(server);
  }

  if (mode !== "cluster") {
    registerAppIndexTools(server);
  }

  return server;
}
