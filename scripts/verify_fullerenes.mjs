// Real stdio MCP handshake and structural tool query; no LLM/API call.
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { Client } from '../.runtime/agent-tools/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../.runtime/agent-tools/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const client = new Client({ name: 'cloud-monitor-verification', version: '1.0.0' });
const transport = new StdioClientTransport({ command: 'bash', args: [resolve(root, 'scripts/fullerenes.sh'), 'mcp', root] });
try {
  await client.connect(transport);
  const list = await client.listTools();
  if (!list.tools.some(t => t.name === 'query_codebase')) throw new Error('Missing query_codebase');
  const result = await client.callTool({ name: 'query_codebase', arguments: {
    question: 'Where is MeasurementCollector implemented and how does metrics_server expose metrics?', maxTokens: 600,
  } });
  const output = JSON.stringify(result);
  if (result.isError || !output.includes('src/monitoring/exporter.py')) throw new Error('Structural query failed');
  console.log(`PASS: stdio handshake; ${list.tools.length} tools; query located src/monitoring/exporter.py`);
} finally {
  await client.close();
  await transport.close();
}
