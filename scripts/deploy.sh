#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Garmin Dashboard — One-shot deploy script
# Ubuntu 22.04 / azureuser
#
# Usage (from project root on the VM):
#   bash scripts/deploy.sh
#
# Prerequisites:
#   • .env filled in (copy from .env.example)
#   • Port 80 open in Azure NSG (needed for Let's Encrypt)
#   • DNS resolving: your-vm.region.cloudapp.azure.com → VM IP
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
BLUE="\033[0;34m"
BOLD="\033[1m"
RESET="\033[0m"

log_info()    { echo -e "${BLUE}[INFO]   ${RESET} $*"; }
log_success() { echo -e "${GREEN}[✅ OK]  ${RESET} $*"; }
log_error()   { echo -e "${RED}[❌ ERR] ${RESET} $*" >&2; }
log_warn()    { echo -e "${YELLOW}[⚠  WARN]${RESET} $*"; }

section() {
  echo ""
  echo -e "${BOLD}${BLUE}════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${BLUE}  $*${RESET}"
  echo -e "${BOLD}${BLUE}════════════════════════════════════════${RESET}"
}

# ── Result tracking ───────────────────────────────────────────────────────────
STEPS_OK=()
STEPS_FAIL=()
step_ok()   { STEPS_OK+=("$1"); }
step_fail() { STEPS_FAIL+=("$1"); }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — PREREQUISITES
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 1 — Prerequisite checks"

# Ubuntu check
if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
  log_error "This script requires Ubuntu."
  log_error "Detected: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 || echo 'unknown')"
  exit 1
fi
log_success "Running on Ubuntu $(grep VERSION_ID /etc/os-release | cut -d= -f2 | tr -d '"')"

# .env check
if [[ ! -f "$ENV_FILE" ]]; then
  log_error ".env not found at $ENV_FILE"
  log_error "Run: cp .env.example .env   then fill in your values."
  exit 1
fi
log_success ".env found at $ENV_FILE"

# Source .env (export all vars)
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
log_success ".env loaded"

# Required variable check
REQUIRED_VARS=(FQDN POSTGRES_PASSWORD TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ANTHROPIC_API_KEY)
MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    MISSING+=("$var")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  log_error "Missing required variables in .env: ${MISSING[*]}"
  log_error "Open .env and fill in the blanks, then re-run."
  exit 1
fi
log_success "Required vars present (FQDN=$FQDN)"

# Auto-generate N8N_ENCRYPTION_KEY if empty
if [[ -z "${N8N_ENCRYPTION_KEY:-}" ]]; then
  log_warn "N8N_ENCRYPTION_KEY is empty — generating one automatically..."
  NEW_KEY="$(openssl rand -hex 32)"
  if grep -q "^N8N_ENCRYPTION_KEY=" "$ENV_FILE"; then
    sed -i "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=${NEW_KEY}|" "$ENV_FILE"
  else
    echo "N8N_ENCRYPTION_KEY=${NEW_KEY}" >> "$ENV_FILE"
  fi
  export N8N_ENCRYPTION_KEY="$NEW_KEY"
  log_success "N8N_ENCRYPTION_KEY generated and saved to .env"
else
  log_success "N8N_ENCRYPTION_KEY present"
fi

step_ok "Prerequisites"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — DOCKER
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 2 — Docker"

if command -v docker &>/dev/null; then
  log_success "Docker already installed: $(docker --version)"
else
  log_info "Docker not found — installing via get.docker.com..."
  curl -fsSL https://get.docker.com | sh
  log_success "Docker installed: $(docker --version)"
fi

# Add current user to docker group (idempotent)
if ! groups "$USER" | grep -qw docker; then
  log_info "Adding $USER to docker group..."
  sudo usermod -aG docker "$USER"
  log_warn "Group membership updated. Re-executing script under docker group..."
  # Re-exec the entire script under the docker group so the rest runs correctly.
  # On second pass, 'groups' will include docker and we skip this block.
  exec sg docker "bash $(realpath "$0")"
fi

# Verify docker compose v2
if ! docker compose version &>/dev/null; then
  log_error "docker compose (v2) not available. Docker installation may be incomplete."
  step_fail "Docker"
  exit 1
fi
log_success "docker compose $(docker compose version --short) available"

# Sanity: can we actually talk to the daemon?
if ! docker info &>/dev/null; then
  log_error "Cannot connect to Docker daemon. Try: sudo systemctl start docker"
  step_fail "Docker"
  exit 1
fi

step_ok "Docker"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — SSL CERTIFICATE (Let's Encrypt / certbot standalone)
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 3 — SSL Certificate (Let's Encrypt)"

CERT_PATH="/etc/letsencrypt/live/${FQDN}/fullchain.pem"

if [[ -f "$CERT_PATH" ]]; then
  log_success "Certificate already exists for $FQDN — skipping issuance"
  step_ok "SSL Certificate (already present)"
else
  log_info "Requesting certificate for $FQDN via certbot standalone..."
  log_warn "Certbot will bind to port 80 for the ACME challenge."

  # Stop nginx if it's already running (it would fight for port 80)
  docker compose -f "$PROJECT_DIR/docker-compose.yml" stop nginx 2>/dev/null || true

  if docker run --rm \
      -p 80:80 \
      -v /etc/letsencrypt:/etc/letsencrypt \
      -v /var/lib/letsencrypt:/var/lib/letsencrypt \
      certbot/certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email "admin@example.com" \
        -d "$FQDN"; then
    log_success "Certificate issued successfully for $FQDN"
    step_ok "SSL Certificate"
  else
    log_error "Certbot failed. Common causes:"
    log_error "  • Port 80 not reachable from internet (check Azure NSG)"
    log_error "  • DNS not yet pointing to this IP  →  dig +short $FQDN"
    log_error "  • Let's Encrypt rate limit hit (5 certs/domain/week)"
    log_error "Fix the issue and re-run deploy.sh."
    step_fail "SSL Certificate"
    exit 1
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — DOCKER COMPOSE UP
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 4 — docker compose up"

cd "$PROJECT_DIR"

log_info "Building images and starting all services..."
docker compose up -d --build
log_success "docker compose up completed"

# Wait for postgres healthy
log_info "Waiting for postgres to become healthy (timeout: 60s)..."
TIMEOUT=60
ELAPSED=0
INTERVAL=5

while true; do
  PG_STATUS="$(docker compose ps postgres 2>/dev/null | grep -o 'healthy' || true)"
  if [[ "$PG_STATUS" == "healthy" ]]; then
    log_success "postgres is healthy ✓"
    break
  fi

  if (( ELAPSED >= TIMEOUT )); then
    log_error "postgres did not become healthy within ${TIMEOUT}s"
    log_error "Last 30 lines of postgres logs:"
    docker compose logs postgres --tail=30
    step_fail "Docker Compose (postgres timeout)"
    exit 1
  fi

  log_info "  postgres not ready yet — ${ELAPSED}s elapsed, retrying in ${INTERVAL}s..."
  sleep "$INTERVAL"
  (( ELAPSED += INTERVAL ))
done

step_ok "Docker Compose (all services up)"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 5 — Health checks"

# Give containers a moment to fully initialize
log_info "Waiting 8s for services to stabilize..."
sleep 8

# Check 1: analyzer FastAPI /health
# analyzer uses `expose` (Docker-internal only) — reach it via docker exec, not localhost
log_info "Checking analyzer API → docker exec → /health"
ANALYZER_HEALTH="$(docker compose exec -T analyzer \
    curl -sf http://localhost:8000/health 2>/dev/null || echo '{}')"
if echo "$ANALYZER_HEALTH" | grep -q '"status":"ok"'; then
  log_success "Analyzer API: /health → ok (db connected)"
  step_ok "Analyzer health"
else
  log_warn "Analyzer /health response: $ANALYZER_HEALTH (may still be starting)"
  step_fail "Analyzer health"
fi

# Check 2: HTTPS via FQDN
log_info "Checking HTTPS → https://$FQDN"
HTTP_CODE="$(curl -sI -o /dev/null -w '%{http_code}' --max-time 10 "https://${FQDN}" 2>/dev/null || echo 'FAIL')"
if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "301" || "$HTTP_CODE" == "302" ]]; then
  log_success "HTTPS: https://$FQDN → HTTP $HTTP_CODE"
  step_ok "HTTPS reachable"
else
  log_warn "HTTPS returned HTTP $HTTP_CODE (nginx or DNS may need a moment)"
  step_fail "HTTPS check (HTTP $HTTP_CODE)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6 — SSL RENEWAL CRON
# ═══════════════════════════════════════════════════════════════════════════════
section "STEP 6 — SSL auto-renewal cron"

# Runs on the 1st of every month at 03:00.
# pre-hook: stop nginx so certbot can bind port 80.
# post-hook: bring nginx back up.
RENEW_CMD="0 3 1 * * docker run --rm -p 80:80 \
-v /etc/letsencrypt:/etc/letsencrypt \
-v /var/lib/letsencrypt:/var/lib/letsencrypt \
certbot/certbot renew \
--pre-hook 'docker compose -f ${PROJECT_DIR}/docker-compose.yml stop nginx' \
--post-hook 'docker compose -f ${PROJECT_DIR}/docker-compose.yml start nginx' \
>> /var/log/certbot-renew.log 2>&1"

if crontab -l 2>/dev/null | grep -q "certbot renew"; then
  log_success "SSL renewal cron already configured — skipping"
else
  log_info "Adding monthly SSL renewal cron (1st of month, 03:00)..."
  ( crontab -l 2>/dev/null; echo "$RENEW_CMD" ) | crontab -
  log_success "Cron added"
fi

step_ok "SSL Renewal Cron"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7 — SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${BLUE}════════════════════════════════════════${RESET}"
echo -e "${BOLD}${BLUE}           DEPLOY SUMMARY               ${RESET}"
echo -e "${BOLD}${BLUE}════════════════════════════════════════${RESET}"

if [[ ${#STEPS_OK[@]} -gt 0 ]]; then
  echo ""
  echo -e "${GREEN}${BOLD}Succeeded:${RESET}"
  for s in "${STEPS_OK[@]}"; do
    echo -e "  ${GREEN}✅${RESET} $s"
  done
fi

if [[ ${#STEPS_FAIL[@]} -gt 0 ]]; then
  echo ""
  echo -e "${RED}${BOLD}Failed / Warnings:${RESET}"
  for s in "${STEPS_FAIL[@]}"; do
    echo -e "  ${RED}❌${RESET} $s"
  done
fi

echo ""
echo -e "${BOLD}${GREEN}🌐  n8n UI:${RESET}   https://${FQDN}"
echo -e "${BOLD}${GREEN}🔑  Login:${RESET}    ${N8N_BASIC_AUTH_USER:-admin} / (N8N_BASIC_AUTH_PASS in .env)"
echo ""
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${YELLOW}  ONE MANUAL STEP REMAINING${RESET}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "  1. Open  ${BLUE}https://${FQDN}${RESET}"
echo -e "  2. Settings → Import from file → ${BOLD}n8n/garmin_workflow.json${RESET}"
echo -e "  3. Activate the workflow"
echo ""
echo -e "${BOLD}${YELLOW}📂  Initial data load (run once — loads Garmin history into PostgreSQL):${RESET}"
echo -e "  ${BLUE}# Copy your CSV to the project dir first, then:${RESET}"
echo -e "  ${BLUE}docker compose run --rm \\${RESET}"
echo -e "  ${BLUE}  -v \$(pwd)/Activities.csv:/data/Activities.csv \\${RESET}"
echo -e "  ${BLUE}  analyzer \\${RESET}"
echo -e "  ${BLUE}  python initial_load.py /data/Activities.csv${RESET}"
echo ""

if [[ ${#STEPS_FAIL[@]} -eq 0 ]]; then
  log_success "All steps completed successfully. 🎉 The bot is live!"
else
  log_warn "Deploy finished with warnings — review the ❌ steps above."
fi
