# OpenClaw Docker Demo

## Clearing Chat History

The OpenClaw UI does not have a built-in way to clear visible chat messages. `/reset` and `/new` only clear the AI context, not the displayed messages. The main session (`agent:main:main`) cannot be deleted from the Sessions page.

To get a clean slate without losing pairing (Nevermined login, agent registration, plan setup):

1. Find the session transcript file inside the container:
   ```bash
   docker exec nvm-seller find /root/.openclaw/agents/main/sessions -name "*.jsonl"
   ```

2. Truncate it:
   ```bash
   docker exec nvm-seller truncate -s 0 /root/.openclaw/agents/main/sessions/<session-id>.jsonl
   ```

3. Hard-refresh the browser (`Cmd+Shift+R`).

Same applies to the buyer container — replace `nvm-seller` with `nvm-buyer`.

## Container Names

- `nvm-seller` — Seller UI at http://localhost:18789
- `nvm-buyer` — Buyer UI at http://localhost:18790

## Session Storage

- Session config: `/root/.openclaw/agents/main/sessions/sessions.json`
- Chat transcript: `/root/.openclaw/agents/main/sessions/<uuid>.jsonl`
- Plugin/pairing state is stored separately and survives transcript clearing.
