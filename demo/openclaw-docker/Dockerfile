FROM node:22-slim

# Install system dependencies required by OpenClaw installer
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw CLI (skip onboarding wizard and interactive prompts)
ENV OPENCLAW_NO_PROMPT=1
ENV OPENCLAW_NO_ONBOARD=1
RUN curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard --no-prompt

# Add openclaw to PATH (installed globally via npm)
ENV PATH="/root/.local/bin:/root/.openclaw/bin:${PATH}"

# Update to latest version
RUN openclaw update --yes 2>/dev/null || true

# Install Nevermined plugin from local tarball and its peer dependency
COPY nevermined-plugin.tgz /tmp/nevermined-plugin.tgz
RUN mkdir -p /root/.openclaw/extensions/openclaw-plugin && \
    tar xzf /tmp/nevermined-plugin.tgz -C /root/.openclaw/extensions/openclaw-plugin --strip-components=1 && \
    cd /root/.openclaw/extensions/openclaw-plugin && npm install @nevermined-io/payments && \
    rm /tmp/nevermined-plugin.tgz

# Create config directory
RUN mkdir -p /root/.openclaw

# Copy config, workspace files, and entrypoint
COPY openclaw.json /opt/openclaw/openclaw.json
COPY TOOLS.md /opt/openclaw/TOOLS.md
COPY entrypoint.sh /opt/openclaw/entrypoint.sh
RUN chmod +x /opt/openclaw/entrypoint.sh

WORKDIR /root

EXPOSE 18789

ENTRYPOINT ["/opt/openclaw/entrypoint.sh"]
