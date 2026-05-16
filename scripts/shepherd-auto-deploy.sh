#!/bin/bash
# shepherd-auto-deploy.sh — cron-friendly auto-pull + restart for Shepherd on herd peers.
#
# Pattern: only cluster-llm is hand-poked. Other BMS herd peers (lab1-4, nuk1) clone
# growlf/ai-stack and run this script on a daily cron. Main-branch updates propagate
# without operator SSH.
#
# Usage:
#   scripts/shepherd-auto-deploy.sh node         # default — runs shepherd-node only
#   scripts/shepherd-auto-deploy.sh control      # runs shepherd-control only
#   scripts/shepherd-auto-deploy.sh both         # runs both (cluster-llm pattern)
#
# Env overrides (set in cron line or wrapper):
#   REPO_DIR        — path to ai-stack checkout (default: $HOME/ai-stack)
#   SHEPHERD_NODE_PORT      — default 40118
#   SHEPHERD_CONTROL_PORT   — default 40117
#   SHEPHERD_NODE_NAME      — display name on dashboard (default: hostname)
#   SHEPHERD_NODE_ADDRESS   — IP advertised to control plane
#   OLLAMA_URL              — local Ollama (default: http://localhost:11434)
#   OLLA_URL                — local Olla (default: http://localhost:40114)
#   SHEPHERD_PEERS          — for control role: comma-separated name=url list
#   OLLA_URLS               — for control role: comma-separated Olla URLs to federate
#
# Cron entry on a BMS lab node (daily at 4:17am, off-peak, non-:00):
#   17 4 * * * /home/<user>/ai-stack/scripts/shepherd-auto-deploy.sh node \
#       >> /tmp/shepherd-auto-deploy.log 2>&1

set -eu

ROLE="${1:-node}"
REPO_DIR="${REPO_DIR:-$HOME/ai-stack}"
LOG_PREFIX="$(date -Iseconds) shepherd-auto-deploy[$ROLE]"

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "$LOG_PREFIX ERROR: $REPO_DIR is not a git checkout. Clone growlf/ai-stack first."
    exit 1
fi

cd "$REPO_DIR"

# Fetch + compare. Skip rebuild if no upstream changes.
git fetch --quiet origin main
LOCAL_REV="$(git rev-parse HEAD)"
REMOTE_REV="$(git rev-parse origin/main)"

if [ "$LOCAL_REV" = "$REMOTE_REV" ]; then
    echo "$LOG_PREFIX no upstream changes ($(git rev-parse --short HEAD))"
    # Still verify processes are running — restart if they died
    NEED_RESTART=0
    if [ "$ROLE" = "node" ] || [ "$ROLE" = "both" ]; then
        if ! pgrep -f "shepherd_node" > /dev/null; then
            echo "$LOG_PREFIX shepherd-node not running, will restart"
            NEED_RESTART=1
        fi
    fi
    if [ "$ROLE" = "control" ] || [ "$ROLE" = "both" ]; then
        if ! pgrep -f "shepherd_control" > /dev/null; then
            echo "$LOG_PREFIX shepherd-control not running, will restart"
            NEED_RESTART=1
        fi
    fi
    if [ "$NEED_RESTART" = "0" ]; then
        exit 0
    fi
else
    echo "$LOG_PREFIX pulling main: $(git rev-parse --short $LOCAL_REV) -> $(git rev-parse --short $REMOTE_REV)"
    git pull --ff-only origin main
fi

# Ensure venv + deps. Always pip-install in case requirements changed.
cd "$REPO_DIR/shepherd"
if [ ! -d .venv ]; then
    echo "$LOG_PREFIX creating venv"
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# Stop old processes
if [ "$ROLE" = "node" ] || [ "$ROLE" = "both" ]; then
    pkill -f shepherd_node 2>/dev/null || true
fi
if [ "$ROLE" = "control" ] || [ "$ROLE" = "both" ]; then
    pkill -f shepherd_control 2>/dev/null || true
fi
sleep 1

# Start new ones
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLA_URL="${OLLA_URL:-http://localhost:40114}"

if [ "$ROLE" = "node" ] || [ "$ROLE" = "both" ]; then
    export SHEPHERD_NODE_PORT="${SHEPHERD_NODE_PORT:-40118}"
    export SHEPHERD_NODE_NAME="${SHEPHERD_NODE_NAME:-$(hostname -s)}"
    export SHEPHERD_NODE_ADDRESS="${SHEPHERD_NODE_ADDRESS:-$(hostname -I | awk '{print $1}')}"
    nohup .venv/bin/python -m shepherd_node > /tmp/shepherd_node.log 2>&1 &
    echo "$LOG_PREFIX started shepherd-node PID=$! on :$SHEPHERD_NODE_PORT as '$SHEPHERD_NODE_NAME'"
fi

if [ "$ROLE" = "control" ] || [ "$ROLE" = "both" ]; then
    export SHEPHERD_CONTROL_PORT="${SHEPHERD_CONTROL_PORT:-40117}"
    export SHEPHERD_PEERS="${SHEPHERD_PEERS:-$(hostname -s)=http://localhost:${SHEPHERD_NODE_PORT:-40118}}"
    export OLLA_URLS="${OLLA_URLS:-$OLLA_URL}"
    nohup .venv/bin/python -m shepherd_control > /tmp/shepherd_control.log 2>&1 &
    echo "$LOG_PREFIX started shepherd-control PID=$! on :$SHEPHERD_CONTROL_PORT"
fi

echo "$LOG_PREFIX deploy complete at $(git rev-parse --short HEAD)"
