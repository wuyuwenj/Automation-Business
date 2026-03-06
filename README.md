# Automation Business

A marketplace-style collection of buyer and seller AI agents built around Nevermined payments, x402, A2A, and AWS deployment paths.

The repo is centered on one buyer experience and several sell-side agents:

- A buyer agent that discovers sellers, evaluates options, purchases results, and exposes a React chat UI.
- Multiple seller agents that monetize data, research, scraping, crypto intelligence, and creative generation.
- Supporting examples for MCP, Strands, evaluation, and deployment workflows.

## Live Demo

- Buyer web frontend: https://kwckssvkpx.us-west-2.awsapprunner.com/

The hosted buyer UI is backed by the buyer web server in [`agents/buyer-simple-agent`](./agents/buyer-simple-agent/). It also supports ZeroClick offer placement through the server-side `/api/zeroclick/offers` integration when `ZEROCLICK_API_KEY` is configured.

## Repo Map

### Buyer Agent

| Agent | What it does | Interfaces | Link |
| --- | --- | --- | --- |
| `buyer-simple-agent` | Discovers sellers, filters/selects them, buys results over x402 or A2A, tracks budget/ledger, and powers the web UI | CLI, FastAPI, SSE chat UI, App Runner, AgentCore, LangGraph, TS example | [README](./agents/buyer-simple-agent/README.md) |

### Seller Agents

| Agent | What it sells | Main tools / endpoints | Interfaces | Link |
| --- | --- | --- | --- | --- |
| `seller-simple-agent` | General research/data responses | `search_data`, `summarize_data`, `research_data` via `/data` | x402 HTTP, A2A, AgentCore, LangGraph, TS example | [README](./agents/seller-simple-agent/README.md) |
| `creative-gen-agent` | Creative assets | ad copy, brand strategy, landing page generation via `/creative` | x402 HTTP, A2A, AgentCore | [creative-gen-agent](./agents/creative-gen-agent/) |
| `crypto-market-agent` | Crypto and DeFi intelligence | `price_check`, `market_analysis`, `defi_report` via `/data` | x402 HTTP, A2A | [crypto-market-agent](./agents/crypto-market-agent/) |
| `web-scraper-agent` | Paid web extraction | `scrape_url`, `batch_scrape`, `deep_extract` via `/data` | x402 HTTP, A2A | [web-scraper-agent](./agents/web-scraper-agent/) |

### Supporting Agents and Examples

| Project | Purpose | Link |
| --- | --- | --- |
| `agent-evaluator` | Discovers, health-checks, and ranks agents | [agent-evaluator](./agents/agent-evaluator/) |
| `mcp-server-agent` | Monetized MCP tools with Nevermined | [README](./agents/mcp-server-agent/README.md) |
| `strands-simple-agent` | Minimal Strands + x402 reference implementation | [README](./agents/strands-simple-agent/README.md) |

## Marketplace Flow

1. Start one or more seller agents.
2. Run the buyer agent in CLI or web mode.
3. The buyer discovers sellers from A2A registration or marketplace discovery.
4. The buyer checks plans/budget, acquires payment tokens, then purchases the selected seller capability.
5. Results, spend, and seller performance are tracked in the buyer ledger and UI.

## Quick Start

### Prerequisites

- Python 3.10+
- Poetry
- Node.js 20+ for the React frontend and TypeScript examples
- Nevermined API key and plan(s)
- OpenAI API key

### Shared Setup

Each agent has its own `.env.example`.

```bash
cd agents/<agent-name>
cp .env.example .env
```

Common variables:

```bash
NVM_API_KEY=sandbox:your-api-key
NVM_ENVIRONMENT=sandbox
NVM_PLAN_ID=did:nv:...
OPENAI_API_KEY=sk-...
```

### Run the Local Buyer + Seller Pair

Seller:

```bash
cd agents/seller-simple-agent
poetry install
poetry run agent-a2a
```

Buyer CLI:

```bash
cd agents/buyer-simple-agent
poetry install
poetry run agent
```

Buyer web server:

```bash
cd agents/buyer-simple-agent
poetry run web
```

Buyer frontend dev server:

```bash
cd agents/buyer-simple-agent/frontend
npm install
npm run dev
```

## Buyer Agent Surfaces

The buyer implementation in [`agents/buyer-simple-agent`](./agents/buyer-simple-agent/) includes:

- Interactive CLI buyer flow: `poetry run agent`
- Web server + React UI: `poetry run web`
- Scripted demos: `poetry run client`, `poetry run client-a2a`, `poetry run demo`
- LangGraph mode: `poetry run agent-langgraph`
- AWS App Runner deployment config: [`agents/buyer-simple-agent/apprunner.yaml`](./agents/buyer-simple-agent/apprunner.yaml)
- AWS AgentCore entrypoint: [`agents/buyer-simple-agent/src/web_agentcore.py`](./agents/buyer-simple-agent/src/web_agentcore.py)
- TypeScript buyer example: [`agents/buyer-simple-agent/ts`](./agents/buyer-simple-agent/ts)

The buyer web server exposes:

- `POST /api/chat`
- `GET /api/sellers`
- `GET /api/balance`
- `GET /api/logs/stream`
- `GET /api/config`
- `GET /api/zeroclick/offers`

## Seller Agent Pricing

| Agent | 1 credit | 5 credits | 10 credits |
| --- | --- | --- | --- |
| `seller-simple-agent` | basic web search | content summarization | full market research |
| `creative-gen-agent` | ad copy bundle | brand strategy brief | landing page HTML |
| `crypto-market-agent` | price check | market trend analysis | DeFi protocol report |
| `web-scraper-agent` | single URL scrape | batch scrape | deep site extraction |

## Protocols and Deployment Modes

- `x402`: payment negotiation over HTTP with `payment-signature` / `payment-required`
- `A2A`: agent card discovery and JSON-RPC agent-to-agent calls
- `AgentCore`: AWS runtime path used by the buyer and selected sellers
- `App Runner`: source-based deployment path for the buyer web app
- `MCP`: separate monetized tool server example in `agents/mcp-server-agent`

## Docs

- [Getting Started](./docs/getting-started.md)
- [AWS Integration](./docs/aws-integration.md)
- [Deploy to AgentCore](./docs/deploy-to-agentcore.md)

## License

MIT
