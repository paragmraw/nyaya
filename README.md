# nyaya

A monorepo for Indian-law tooling. The first component is an MCP server; other tools will follow.

## Components

| Path | Status | Description |
|---|---|---|
| [`mcp/`](mcp/) | alpha | MCP server for Indian law — Constitution, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, commercial statutes, landmark SC judgments. Exposes 24 tools and 11 resources over HTTP. Deployable to Railway via Docker. |

See [`mcp/README.md`](mcp/README.md) for setup, deployment, and client-configuration instructions.

## License

Apache-2.0. See [LICENSE](LICENSE).