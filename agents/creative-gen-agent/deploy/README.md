# EC2 Deployment

This directory contains templates to deploy `agents/creative-gen-agent` on an
Ubuntu EC2 instance behind `nginx` with `systemd`.

## 1. Create the app directory

```bash
sudo mkdir -p /opt/creative-gen-agent
sudo chown "$USER":"$USER" /opt/creative-gen-agent
```

## 2. Copy the project

Clone or copy this repository onto the EC2 host, then place the creative agent
at:

```text
/opt/creative-gen-agent
```

The service template assumes the app root is exactly that path.

## 3. Install system packages

Run:

```bash
bash deploy/ec2-bootstrap.sh
```

This installs:

- `python3.10`
- `python3.10-venv`
- `python3-pip`
- `git`
- `nginx`
- `certbot`
- `python3-certbot-nginx`
- Poetry

## 4. Install Python dependencies

```bash
cd /opt/creative-gen-agent
export PATH="$HOME/.local/bin:$PATH"
poetry env use python3.10
poetry install
```

## 5. Create `.env`

Copy `.env.example` to `.env` and fill in:

```env
NVM_API_KEY=...
NVM_PLAN_ID=...
NVM_AGENT_ID=...
NVM_ENVIRONMENT=sandbox

OPENAI_API_KEY=...
MODEL_ID=gpt-4o-mini

GEMINI_API_KEY=...
GEMINI_MODEL_ID=gemini-2.5-pro
GEMINI_FALLBACK_MODEL_ID=gemini-2.5-flash

PORT=3000

# Optional preview hosting
# LANDING_PAGE_S3_BUCKET=...
# LANDING_PAGE_S3_PREFIX=landing-pages
# LANDING_PAGE_PUBLIC_BASE_URL=https://cdn.example.com
# LANDING_PAGE_URL_EXPIRES_SECONDS=86400
```

## 6. Test the app directly

```bash
cd /opt/creative-gen-agent
poetry run python -m src.agent
```

In another shell:

```bash
curl http://127.0.0.1:3000/health
curl http://127.0.0.1:3000/pricing
```

Stop the foreground process after the test passes.

## 7. Install the `systemd` unit

```bash
sudo cp deploy/creative-gen-agent.service /etc/systemd/system/creative-gen-agent.service
sudo systemctl daemon-reload
sudo systemctl enable creative-gen-agent
sudo systemctl start creative-gen-agent
sudo systemctl status creative-gen-agent
```

Logs:

```bash
sudo journalctl -u creative-gen-agent -f
```

## 8. Configure nginx

Copy the site config:

```bash
sudo cp deploy/nginx-creative-gen-agent.conf /etc/nginx/sites-available/creative-gen-agent
```

Edit `server_name` in that file to your real domain, then:

```bash
sudo ln -sf /etc/nginx/sites-available/creative-gen-agent /etc/nginx/sites-enabled/creative-gen-agent
sudo nginx -t
sudo systemctl restart nginx
```

## 9. Enable HTTPS

Point your DNS `A` record to the EC2 public IP, then run:

```bash
sudo certbot --nginx -d yourdomain.com
```

## 10. Register in Nevermined

Use:

```text
POST https://yourdomain.com/creative
```

as the protected endpoint URL.
