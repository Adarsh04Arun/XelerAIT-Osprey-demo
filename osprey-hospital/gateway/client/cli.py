#!/usr/bin/env python3
"""Terminal client for the hospital agent.

Shows the guardrail decisions inline, because a chat transcript alone tells you
nothing about whether the rules are working -- a blocked turn and a turn the
model simply chose not to take look identical from the outside.

    python -m gateway.client.cli
    python -m gateway.client.cli --patient pat-1001 --verified

Commands inside the session:
    /state        current schedule
    /audit        recent audit entries
    /escalations  what's waiting for a human
    /reset        reset hospital + sessions
    /quit
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import requests

RESET = '\033[0m'
DIM = '\033[2m'
BOLD = '\033[1m'
RED = '\033[31m'
GREEN = '\033[32m'
YELLOW = '\033[33m'
BLUE = '\033[34m'
MAGENTA = '\033[35m'


def render_decisions(decisions: list) -> None:
    for d in decisions:
        stage = d['stage']
        tool = d['tool'] or '-'
        latency = d['latency_ms']

        if d['allowed'] and not d['verdicts']:
            print(f'{DIM}  [osprey] {stage}/{tool}: clean ({latency}ms){RESET}')
            continue

        colour = GREEN if d['allowed'] else RED
        status = 'ALLOW' if d['allowed'] else 'DENY'
        print(f'{colour}  [osprey] {stage}/{tool}: {status} ({latency}ms){RESET}')

        for verdict in d['verdicts']:
            print(f'{colour}           verdict: {verdict}{RESET}')
        for reason in d.get('reasons', []):
            print(f'{DIM}           {reason.get("rule")}: {reason.get("description")}{RESET}')
        if d['timed_out']:
            print(f'{YELLOW}           (guardrail timed out -- failed closed){RESET}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Hospital agent CLI client')
    parser.add_argument('--url', default='http://localhost:8090', help='Gateway base URL')
    parser.add_argument('--session', default=None, help='Session id (default: random)')
    parser.add_argument('--patient', default=None, help='Acting patient id, e.g. pat-1001')
    parser.add_argument(
        '--verified',
        action='store_true',
        help='Treat the caller as identity-verified. Without this, record access is denied.',
    )
    args = parser.parse_args()

    session_id = args.session or f'sess-{uuid.uuid4().hex[:8]}'

    print(f'{BOLD}Ashgrove Family Practice -- scheduling assistant{RESET}')
    print(f'{DIM}session={session_id} patient={args.patient or "(anonymous)"} '
          f'verified={args.verified}{RESET}')
    print(f'{DIM}Guardrail decisions are shown inline. /quit to exit.{RESET}\n')

    while True:
        try:
            message = input(f'{BOLD}you >{RESET} ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not message:
            continue

        if message in ('/quit', '/exit'):
            return 0

        if message in ('/state', '/audit', '/escalations'):
            path = {'/state': '/hospital/state', '/audit': '/audit', '/escalations': '/escalations'}[message]
            try:
                resp = requests.get(f'{args.url}{path}', timeout=15)
                print(f'{DIM}{json.dumps(resp.json(), indent=2)}{RESET}\n')
            except Exception as e:
                print(f'{RED}request failed: {e}{RESET}\n')
            continue

        if message == '/reset':
            try:
                requests.post(f'{args.url}/admin/reset', timeout=15)
                print(f'{GREEN}state reset{RESET}\n')
            except Exception as e:
                print(f'{RED}reset failed: {e}{RESET}\n')
            continue

        try:
            resp = requests.post(
                f'{args.url}/chat',
                json={
                    'message': message,
                    'session_id': session_id,
                    'actor_id': args.patient,
                    'identity_verified': args.verified,
                },
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f'{RED}request failed: {e}{RESET}\n')
            continue

        for call in data.get('tool_calls', []):
            print(f'{BLUE}  [tool] {call["name"]}({json.dumps(call["arguments"])}){RESET}')

        render_decisions(data.get('decisions', []))

        if data.get('escalated'):
            reasons = ', '.join(data.get('escalation_reasons', []))
            print(f'{MAGENTA}  [escalated] {reasons}{RESET}')

        marker = f'{RED}agent (guardrail substituted) >{RESET}' if data.get('blocked') else f'{BOLD}agent >{RESET}'
        print(f'{marker} {data["reply"]}\n')


if __name__ == '__main__':
    sys.exit(main())
