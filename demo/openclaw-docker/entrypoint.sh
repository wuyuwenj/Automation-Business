#!/bin/bash
set -e

# Copy config only on first run (don't overwrite runtime changes like planId/agentId)
if [ ! -f /root/.openclaw/openclaw.json ]; then
  cp /opt/openclaw/openclaw.json /root/.openclaw/openclaw.json
fi
mkdir -p /root/.openclaw/workspace
cp /opt/openclaw/TOOLS.md /root/.openclaw/workspace/TOOLS.md

# Auto-select model based on available API key
if [ -n "$OPENAI_API_KEY" ]; then
  openclaw models set openai/gpt-4o-mini 2>/dev/null || true
  echo "Model: openai/gpt-4o-mini"
elif [ -n "$ANTHROPIC_API_KEY" ]; then
  openclaw models set anthropic/claude-sonnet-4-6 2>/dev/null || true
  echo "Model: anthropic/claude-sonnet-4-6"
fi

echo "=== OpenClaw + Nevermined Demo ==="
echo "Config loaded at /root/.openclaw/openclaw.json"
echo "Starting OpenClaw gateway on port 18789..."
echo ""

# Start the OpenClaw gateway in foreground
exec openclaw gateway --bind lan
