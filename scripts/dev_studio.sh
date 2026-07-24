#!/usr/bin/env bash
# Launch LangGraph Studio locally against this repo's graphs, bypassing any
# machine-level OpenAI gateway config (e.g. LangChain's /etc-managed gateway_env)
# so model calls use the OPENAI_API_KEY / ANTHROPIC_API_KEY in this repo's .env directly.
#
# Usage: ./scripts/dev_studio.sh [--graph <graph_name>]

set -euo pipefail
cd "$(dirname "$0")/.."

unset OPENAI_BASE_URL
unset OPENAI_API_KEY
unset ANTHROPIC_BASE_URL
unset ANTHROPIC_API_KEY
unset ANTHROPIC_CUSTOM_HEADERS

exec uv run langgraph dev "$@"
