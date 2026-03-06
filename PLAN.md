# Best Autonomous Buyer Agent - Implementation Plan

## Goal
Win "Best Autonomous Buyer" at the Nevermined hackathon by building a smart buyer agent with:
- 3+ paid transactions across 2+ different teams
- Repeat purchases / switching after evaluation
- Explicit budget enforcement (already exists)
- ROI-based decision logic
- Mindra orchestration for multi-seller workflows
- ZeroClick ads to offset credit costs

---

## Architecture

```
User ──→ [React Frontend + ZeroClick Ads]
              │
              ▼
         [FastAPI Backend]
              │
              ▼
         [Strands Agent + Smart Buyer Logic]
              │
         ┌────┴─────────────┐
         ▼                  ▼
  [Direct Purchase]   [Mindra Orchestration]
    (single seller)    (multi-seller workflow)
         │                  │
         ▼                  ▼
  [Nevermined x402]   [Mindra API → SSE]
         │                  │
         ▼                  ▼
   Seller Agents      Mindra Workflows
                      (chained agent calls)
```

---

## Implementation Steps

### Step 1: Purchase Ledger (`src/ledger.py`) — NEW FILE

Replace the simple budget purchase list with a rich ledger that tracks evaluation data.

```python
@dataclass
class PurchaseRecord:
    id: str                    # uuid
    query: str
    query_category: str        # classified by LLM: "research", "sentiment", "analysis", etc.
    seller_url: str
    seller_name: str
    cost: int                  # credits spent
    quality_score: float       # 0-8 from rubric evaluation
    roi: float                 # quality_score / cost
    response_summary: str      # first 200 chars of response
    evaluation: dict           # {relevance, depth, actionability, specificity} each 0-2
    evaluation_reasoning: str  # LLM explanation of scores
    timestamp: str             # ISO format

class PurchaseLedger:
    def __init__(self, path: str = "purchase_ledger.json"):
        # Persist to JSON file so it survives restarts

    def record(self, record: PurchaseRecord)

    def get_seller_stats(self, seller_url: str) -> dict:
        # Returns: {avg_quality, avg_roi, total_purchases, success_rate}

    def get_category_stats(self, category: str) -> dict:
        # Returns: {best_seller, sellers_tried, avg_roi_per_seller}

    def get_all_records() -> list[PurchaseRecord]

    def get_summary() -> dict:
        # Returns: {total_purchases, total_spent, avg_roi, by_seller: {...}, by_category: {...}}
```

**Integrates with**: `budget.py` (budget still handles spending limits, ledger handles evaluation/ROI)

---

### Step 2: Marketplace Discovery Tool (`src/tools/discover_marketplace.py`) — NEW FILE

New tool that queries the Nevermined hackathon Discovery API.

```python
@tool
def discover_marketplace(category: str = "") -> dict:
    """Discover sellers from the Nevermined hackathon marketplace.

    Queries the Discovery API, filters out unreachable endpoints (localhost),
    and registers live sellers into the local registry.

    Args:
        category: Optional category filter (e.g. "DeFi", "AI/ML", "Research")
    """
```

**Flow:**
1. `GET https://nevermined.ai/hackathon/register/api/discover` with `x-nvm-api-key` header
2. Optional: `?side=sell&category={category}`
3. Filter out sellers with localhost/internal endpoints
4. For each live seller: create a SellerInfo and register in seller_registry
5. Map Discovery API fields to SellerInfo:
   - `endpointUrl` → `url`
   - `name` → `name`
   - `description` → `description`
   - `keywords` → synthesize into `skills`
   - `planIds[0]` → `plan_id`
   - `nvmAgentId` → `agent_id`
   - `pricing.perRequest` → `cost_description`
6. Return list of registered sellers with count

**Note:** Also extend `SellerInfo` and `SellerRegistry` to store `keywords: list[str]` and `category: str` from the marketplace data — needed for filtering.

---

### Step 3: Seller Filtering Tool (`src/tools/filter_sellers.py`) — NEW FILE

Pre-purchase intelligence: match sellers to a query WITHOUT spending credits.

```python
@tool
def filter_sellers(query: str) -> dict:
    """Find the most relevant sellers for a query based on their descriptions and keywords.

    Uses seller metadata (keywords, description, category, skills) to rank
    relevance. No credits are spent — this is free pre-purchase intelligence.

    Args:
        query: The user's query to match against seller capabilities.
    """
```

**Scoring logic:**
- Keyword overlap: count how many seller keywords appear in the query (or are semantically similar)
- Category match: exact or partial match
- Description relevance: simple word overlap or let the LLM score it
- Return top 2-3 sellers sorted by relevance score
- Include reasoning: "Matched on keywords: [search, research]. Category: Research."

**Alternative approach:** Instead of a separate tool, this could be handled entirely in the system prompt — tell the agent to read seller descriptions from `list_sellers()` and pick the most relevant. This is simpler and the LLM is good at this. We'll use the tool approach for explicit logging but the LLM does the actual matching.

---

### Step 4: Evaluate Response Tool (`src/tools/evaluate.py`) — NEW FILE

Post-purchase evaluation using a structured rubric.

```python
@tool
def evaluate_purchase(query: str, seller_name: str, seller_url: str, response_text: str, credits_spent: int) -> dict:
    """Evaluate the quality of a purchased response using a structured rubric.

    Scores the response on 4 dimensions (0-2 each, max 8), calculates ROI,
    and records the evaluation in the purchase ledger.

    Args:
        query: The original query that was sent.
        seller_name: Name of the seller.
        seller_url: URL of the seller.
        response_text: The response received from the seller.
        credits_spent: Number of credits spent on this purchase.
    """
```

**Rubric (scored by the agent itself in the system prompt):**
```
1. Relevance (0-2): Did it directly answer the query?
2. Depth (0-2): Did it include specific data, facts, or numbers?
3. Actionability (0-2): Could the user make a decision from this?
4. Specificity (0-2): Was it beyond generic/boilerplate?
```

**Flow:**
1. Agent calls this tool after each purchase with the rubric scores
2. Tool calculates ROI = total_score / credits_spent
3. Records a `PurchaseRecord` in the ledger
4. Updates seller stats in registry (optional: add stats fields to SellerInfo)
5. Returns: evaluation summary + ROI + comparison with past purchases from same/other sellers
6. Logs evaluation clearly (judges need to see this)

---

### Step 5: Seller Selection Tool (`src/tools/select_seller.py`) — NEW FILE

Explore/exploit logic with logged reasoning.

```python
@tool
def select_seller(query: str) -> dict:
    """Select the best seller for a query using explore/exploit logic.

    Checks purchase history, compares seller ROI, and decides whether to
    use a proven seller or explore a new one. Logs reasoning explicitly.

    Args:
        query: The user's query to find the best seller for.
    """
```

**Algorithm:**
1. Get available sellers from registry
2. Filter to relevant sellers (by keywords/description)
3. Classify query category
4. Check ledger for this category:
   - **No history** → EXPLORE: pick cheapest relevant seller. Reason: "No purchase history for this category. Starting with cheapest seller to minimize exploration cost."
   - **< 2 sellers tried** → EXPLORE: try a different seller. Reason: "Only tried 1 seller for [category]. Trying [seller B] for comparison."
   - **2+ sellers tried** → EXPLOIT (80%) or EXPLORE (20%):
     - EXPLOIT reason: "Choosing [seller A] (avg ROI 7.5) over [seller B] (avg ROI 5.0). Higher quality per credit in 3 previous purchases."
     - EXPLORE reason: "Periodic re-evaluation. Trying [seller C] to check if better options exist."
5. Return: selected seller URL + name + reasoning

---

### Step 6: Mindra Orchestration Tool (`src/tools/orchestrate.py`) — NEW FILE

Use Mindra to run multi-seller workflows.

```python
@tool
def run_research_workflow(query: str) -> dict:
    """Run a multi-step research workflow using Mindra orchestration.

    Triggers a Mindra workflow that coordinates queries across multiple
    seller agents and returns synthesized results.

    Args:
        query: The research query to investigate across multiple sources.
    """
```

**Flow:**
1. `POST https://api.mindra.co/v1/workflows/{workflow_slug}/run`
   - Header: `x-api-key: MINDRA_API_KEY`
   - Body: `{"task": query, "metadata": {"buyer_agent": true}}`
2. Get `execution_id` and `stream_url` from response
3. Connect to SSE: `GET https://api.mindra.co{stream_url}`
4. Parse events: `chunk`, `tool_executing`, `tool_result`, `done`
5. On `done`: extract `final_answer`
6. Return synthesized result

**Env vars needed:**
- `MINDRA_API_KEY` — from Mindra console
- `MINDRA_WORKFLOW_SLUG` — the workflow slug (e.g. `basic-search-agent` or a custom one)

**Note:** The workflow itself is configured in the Mindra Console. We just trigger and stream results. The user would need to set up a workflow in Mindra that chains together multiple seller calls.

---

### Step 7: ZeroClick Ads in Frontend (`frontend/src/components/AdBanner.tsx`) — NEW FILE

Add ad monetization to offset credit costs.

```tsx
// Simple ad banner component
export default function AdBanner() {
    // ZeroClick integration
    // Renders ad placements in the buyer frontend
    // Revenue offsets the cost of purchasing from sellers
}
```

**Placement options:**
- Below the seller sidebar
- Between chat messages (every N messages)
- In the activity log header area

**Integration:** Need ZeroClick SDK/script tag + ad placement IDs. The user will need to provide ZeroClick credentials.

**Note:** This is a frontend-only change. Minimal — just a component + script tag in index.html.

---

### Step 8: Update System Prompt (`src/strands_agent.py`)

Rewrite the A2A system prompt to include the new buying logic:

```
You are an autonomous data buying agent with smart purchasing logic.
You evaluate sellers, track ROI, and make intelligent buying decisions.

Your workflow:
1. **discover_marketplace** — Find sellers from the hackathon marketplace.
2. **list_sellers** — See all registered sellers and their capabilities.
3. **filter_sellers** or read seller descriptions to identify relevant sellers.
4. **select_seller** — Use explore/exploit logic to pick the best seller.
5. **check_balance** — Verify budget before purchasing.
6. **purchase_a2a** — Buy from the selected seller.
7. **evaluate_purchase** — Score the response quality and record ROI.

For complex queries needing multiple sources:
8. **run_research_workflow** — Use Mindra to orchestrate multi-seller research.

After purchasing, ALWAYS evaluate the response using this rubric:
- Relevance (0-2): Did it directly answer the query?
- Depth (0-2): Did it include specific data/facts/numbers?
- Actionability (0-2): Could the user make a decision from this?
- Specificity (0-2): Was it beyond generic/boilerplate?

Decision logic:
- First time buying in a category → try cheapest relevant seller (EXPLORE)
- Tried only 1 seller → try a different one for comparison (EXPLORE)
- Tried 2+ sellers → use highest ROI seller (EXPLOIT), re-check every 3rd purchase
- Always log your reasoning for choosing a seller.

Budget rules:
- Always check balance before buying.
- Report expected cost before purchasing.
- If budget exceeded, explain and suggest alternatives.
```

**Also update:**
- `_A2A_TOOLS` list: add new tools (discover_marketplace, filter_sellers, select_seller, evaluate_purchase, run_research_workflow)
- `create_agent()`: wire up the new tools

---

### Step 9: Update Registry (`src/registry.py`)

Add fields to SellerInfo for marketplace data:

```python
@dataclass
class SellerInfo:
    url: str
    name: str
    description: str
    skills: list[dict]
    plan_id: str = ""
    agent_id: str = ""
    credits: int = 1
    cost_description: str = ""
    # NEW fields:
    keywords: list[str] = field(default_factory=list)
    category: str = ""
    team_name: str = ""
```

Add method to register from marketplace data (not just agent card):

```python
def register_from_marketplace(self, seller_data: dict) -> SellerInfo:
    """Register a seller from the Discovery API response format."""
```

---

### Step 10: New API Endpoints (`src/web.py`)

Add endpoints for the frontend to access evaluation/ROI data:

```python
GET /api/ledger          # Returns purchase ledger summary + all records
GET /api/ledger/sellers  # Returns per-seller stats (avg ROI, purchase count)
```

---

### Step 11: Frontend — Purchase History Panel (optional, nice to have)

Add a new component showing:
- Purchase history table (query, seller, cost, quality score, ROI)
- Per-seller ROI comparison
- Budget status bar

This makes the evaluation logic VISIBLE to judges during demo.

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/ledger.py` | NEW | Purchase ledger with evaluation + ROI tracking |
| `src/tools/discover_marketplace.py` | NEW | Discovery API integration |
| `src/tools/filter_sellers.py` | NEW | Pre-purchase seller matching |
| `src/tools/evaluate.py` | NEW | Post-purchase rubric evaluation |
| `src/tools/select_seller.py` | NEW | Explore/exploit seller selection |
| `src/tools/orchestrate.py` | NEW | Mindra workflow orchestration |
| `src/registry.py` | EDIT | Add keywords, category, team_name fields + marketplace registration |
| `src/strands_agent.py` | EDIT | New system prompt + wire up new tools |
| `src/web.py` | EDIT | Add /api/ledger endpoints |
| `frontend/src/components/AdBanner.tsx` | NEW | ZeroClick ad component |
| `frontend/src/App.tsx` | EDIT | Add AdBanner placement |
| `.env.example` | EDIT | Add MINDRA_API_KEY, MINDRA_WORKFLOW_SLUG |

## Implementation Order

1. **Ledger** (foundation — everything else writes to it)
2. **Registry update** (add marketplace fields)
3. **Marketplace discovery tool** (populate sellers)
4. **Evaluate tool** (post-purchase scoring)
5. **Select seller tool** (explore/exploit)
6. **System prompt update** (tie it all together)
7. **Web endpoints** (expose ledger data)
8. **Mindra orchestration** (multi-seller workflows)
9. **ZeroClick ads** (frontend monetization)
10. **Frontend purchase history** (optional, demo polish)

## Env Vars Needed

```bash
# Existing
NVM_API_KEY=sandbox:...
NVM_ENVIRONMENT=sandbox
NVM_PLAN_ID=...

# New
MINDRA_API_KEY=...              # From console.mindra.co
MINDRA_WORKFLOW_SLUG=basic-search-agent  # Or custom workflow
```
