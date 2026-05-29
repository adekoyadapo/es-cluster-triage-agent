import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { registerClusterHealthTools } from "./tools/cluster-health.js";
import { registerResourcePressureTools } from "./tools/resource-pressure.js";
import { registerIndexPressureTools } from "./tools/index-pressure.js";
import { registerIndexLifecycleTools } from "./tools/index-lifecycle.js";
import { registerLogsSecurityTools } from "./tools/logs-security.js";
import { registerTimelineTools } from "./tools/timeline-tools.js";

export function createServer(): McpServer {
  const server = new McpServer({
    name: "elastic-cluster-triage-agent",
    version: "1.0.0",
  });

  registerClusterHealthTools(server);
  registerResourcePressureTools(server);
  registerIndexPressureTools(server);
  registerIndexLifecycleTools(server);
  registerLogsSecurityTools(server);
  registerTimelineTools(server);

  return server;
}
