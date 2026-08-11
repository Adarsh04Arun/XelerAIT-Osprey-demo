#!/usr/bin/env bash
# Bring up the hospital agent on top of the full Osprey stack.
#
#   ./osprey-hospital/run.sh          start everything
#   ./osprey-hospital/run.sh down     stop and remove volumes
#   ./osprey-hospital/run.sh logs     tail the worker + gateway
#
# Run from the OSPREY REPO ROOT (the directory holding docker-compose.yaml).
set -euo pipefail

COMPOSE="docker compose -f docker-compose.yaml -f osprey-hospital/docker-compose.hospital.yaml"

if [[ ! -f docker-compose.yaml ]]; then
  echo "error: run this from the osprey repo root" >&2
  exit 1
fi

case "${1:-up}" in
  down)
    $COMPOSE down -v
    exit 0
    ;;
  logs)
    $COMPOSE logs -f osprey-worker hospital-gateway
    exit 0
    ;;
esac

# --- preflight -------------------------------------------------------------

echo "==> checking Ollama"
if ! curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null; then
  echo "error: Ollama is not responding on localhost:11434." >&2
  echo "       start it, then: ollama pull llama3.1" >&2
  exit 1
fi

if ! curl -s --max-time 5 http://localhost:11434/api/tags | grep -q 'llama3.1'; then
  echo "error: llama3.1 not found. Run: ollama pull llama3.1" >&2
  exit 1
fi
echo "    ollama ok"

# --- build -----------------------------------------------------------------

# Dockerfile.worker builds FROM this image, so it has to exist first.
echo "==> building base osprey-worker image (slow on first run)"
docker build -f osprey_worker/Dockerfile -t osprey-worker:local .

# Only the services the guardrail gate actually needs. The stock Osprey compose
# also brings up six Druid containers plus the analytics UI; together with an 8B
# model loading ~5GB on the host that overruns a default Docker memory
# allocation, and containers start dying with exit 255 (not even flagged as OOM,
# which makes it a confusing failure to chase).
#
# Druid/UI are for querying event history, not for evaluating rules. Add them
# with `--with-ui` if you want the Osprey UI and have the headroom.
SERVICES="osprey-worker hospital-gateway postgres snowflake-id-worker"
if [[ "${2:-}" == "--with-ui" || "${1:-}" == "--with-ui" ]]; then
  echo "==> including Druid + Osprey UI (needs roughly 8GB free for Docker)"
  SERVICES="$SERVICES osprey-ui osprey-ui-api"
fi

echo "==> starting stack"
$COMPOSE up -d --build $SERVICES

echo "==> waiting for the gateway"
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 http://localhost:8090/healthz >/dev/null 2>&1; then
    echo "    gateway ready"
    break
  fi
  sleep 2
done

cat <<'EOF'

Ready.

  Chat (CLI):   python -m gateway.client.cli --patient pat-1001 --verified
                  (run from the osprey-hospital/ directory)
  Gateway API:  http://localhost:8090/docs
  Osprey UI:    http://localhost:5002
  Audit trail:  http://localhost:8090/audit
  Escalations:  http://localhost:8090/escalations

Try these:
  "book me in with dr okafor next tuesday morning"
  "what should I take for this headache?"
  "I've had chest pain since this morning, can I get an appointment?"

EOF
