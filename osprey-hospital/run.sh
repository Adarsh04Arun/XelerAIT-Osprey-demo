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

# The exact tag the gateway will request. Pinned rather than floating -- see the
# note in gateway/app/config.py -- and checked exactly, because a partial match
# is worse than no check: `grep llama3.1` is satisfied by llama3.1:latest, the
# preflight passes, and then the very first chat fails inside Ollama with a
# "model not found" that looks like a gateway bug.
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

echo "==> checking Ollama"
if ! curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null; then
  # Before blaming Ollama, check we are not in WSL. On Windows, `bash` from a
  # PowerShell prompt resolves to C:\Windows\system32\bash.exe -- the WSL
  # launcher -- not Git Bash. WSL2 has its own network namespace, so an Ollama
  # bound to the Windows host's 127.0.0.1 is unreachable from inside it.
  #
  # Worth detecting explicitly because the symptom is a lie: Ollama is running,
  # `ollama pull` just succeeded from the same prompt, and the script says it
  # is not responding. Nothing about the message points at the shell.
  if grep -qi microsoft /proc/version 2>/dev/null; then
    echo "error: running under WSL, which cannot reach Ollama on the Windows host." >&2
    echo "" >&2
    echo "       'bash' from PowerShell is C:\\Windows\\system32\\bash.exe (WSL)," >&2
    echo "       not Git Bash. Ollama is probably running fine -- WSL just can't" >&2
    echo "       see it. Use Git Bash instead:" >&2
    echo "" >&2
    echo "         & \"C:\\Program Files\\Git\\bin\\bash.exe\" osprey-hospital/run.sh" >&2
    echo "" >&2
    echo "       or open a Git Bash terminal and run ./osprey-hospital/run.sh there." >&2
    exit 1
  fi

  echo "error: Ollama is not responding on localhost:11434." >&2
  echo "       start it (the desktop app, or 'ollama serve'), then:" >&2
  echo "       ollama pull ${OLLAMA_MODEL}" >&2
  exit 1
fi

if ! curl -s --max-time 5 http://localhost:11434/api/tags | grep -q "\"${OLLAMA_MODEL}\""; then
  echo "error: ${OLLAMA_MODEL} not found locally." >&2
  echo "       Run: ollama pull ${OLLAMA_MODEL}" >&2
  echo "" >&2
  echo "       Note: llama3.1:latest does NOT satisfy this. Ollama treats tags as" >&2
  echo "       separate entries even when they share a digest, so the pull is" >&2
  echo "       near-instant if you already have :latest -- the blobs are reused." >&2
  echo "       Installed now:" >&2
  curl -s --max-time 5 http://localhost:11434/api/tags \
    | grep -o '"name":"[^"]*"' | sed 's/"name":"/         /;s/"$//' >&2
  exit 1
fi
echo "    ollama ok (${OLLAMA_MODEL})"

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
