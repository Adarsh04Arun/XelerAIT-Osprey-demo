#!/usr/bin/env python3
"""Terminal client for the hospital agent.

Shows the guardrail decisions inline, because a chat transcript alone tells you
nothing about whether the rules are working -- a blocked turn and a turn the
model simply chose not to take look identical from the outside.

Each turn prints three things, in increasing detail:

    [tool]      what the model proposed, and whether it ran
    [osprey]    what the ruleset concluded, and why
    turn flow   the pipeline in order: proposed -> ruled on -> executed or not

The flow is the one that makes the claim checkable. "Nothing reached the
hospital API" is an assertion; a trace with the execute step visibly absent is
evidence.

    python -m gateway.client.cli
    python -m gateway.client.cli --patient pat-1001 --verified
    python -m gateway.client.cli --patient pat-1001 --verified --verbose

Commands inside the session:
    /state        current schedule
    /audit        recent audit entries
    /escalations  what's waiting for a human
    /reset        reset hospital + sessions
    /quit
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
import time
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


@contextlib.contextmanager
def waiting(label: str):
    """Show elapsed time while a request is in flight.

    Inference runs on CPU: the first turn waits ~45s for Ollama to load the
    model, and a turn where the model proposes a tool call needs at least two
    completions, so a minute or more is normal. The guardrail round trip inside
    that is 15-40ms and is never the wait.

    Without a ticking counter, "thinking" and "hung" look identical from the
    prompt, and the reasonable response to a silent terminal is Ctrl+C -- which
    throws away a turn that was about to succeed. The counter is here so the
    number on screen answers the question before you reach for the keyboard.
    """
    stop = threading.Event()
    started = time.monotonic()

    def tick() -> None:
        while not stop.wait(0.5):
            elapsed = time.monotonic() - started
            note = '  (first turn loads the model, ~45s)' if elapsed < 60 else ''
            sys.stdout.write(f'\r{DIM}  {label}... {elapsed:4.0f}s{note}{RESET}\033[K')
            sys.stdout.flush()

    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
        sys.stdout.write('\r\033[K')
        sys.stdout.flush()


# One glyph per pipeline stage, so the shape of a turn is readable at a glance
# without reading any of the text. A denied turn and an allowed one should look
# different from across the room.
_FLOW_MARKS = {
    'user': ('▸', BOLD),
    'model': ('◆', BLUE),
    'identity': ('⇄', YELLOW),
    'gate': ('▣', DIM),
    'execute': ('✔', GREEN),
    'skip': ('✖', RED),
    'reply': ('▸', BOLD),
    'error': ('!', RED),
}


def render_flow(flow: list, verbose: bool = False) -> None:
    """Draw the turn as a pipeline.

    The decision list alone cannot show this. It says what the rules concluded
    but not where in the turn each conclusion landed, and two proposed calls to
    the same tool are indistinguishable in it. The ordering is the part that
    makes the gate legible: proposed -> ruled on -> executed, or proposed ->
    ruled on -> NOT executed. Seeing the third line missing is what makes
    "nothing reached the hospital API" believable rather than asserted.
    """
    if not flow:
        return

    print(f'{DIM}  ┌─ turn flow{RESET}')
    for entry in flow:
        kind = entry.get('kind', '')
        mark, colour = _FLOW_MARKS.get(kind, ('·', DIM))
        detail = entry.get('detail', '')

        # The gate line carries the verdicts; everything else is one line.
        if kind == 'gate':
            allowed = entry.get('allowed')
            colour = DIM if entry.get('ungated') else (GREEN if allowed else RED)

        print(f'{DIM}  │{RESET} {colour}{mark} {detail}{RESET}')

        if kind == 'gate':
            for verdict in entry.get('verdicts', []):
                print(f'{DIM}  │     {colour}{verdict}{RESET}')
            latency = entry.get('latency_ms')
            if latency is not None:
                print(f'{DIM}  │     ({latency}ms){RESET}')

        if verbose and 'arguments' in entry:
            print(f'{DIM}  │     args: {json.dumps(entry["arguments"])}{RESET}')

    print(f'{DIM}  └─{RESET}')


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
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Include tool arguments in the flow trace.',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=300,
        help=(
            'Seconds to wait for a turn. A turn with a tool call needs two or '
            'more completions, and CPU inference makes each one slow.'
        ),
    )
    args = parser.parse_args()

    session_id = args.session or f'sess-{uuid.uuid4().hex[:8]}'

    print(f'{BOLD}XelerAIT -- scheduling assistant{RESET}')
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
            with waiting('thinking'):
                resp = requests.post(
                    f'{args.url}/chat',
                    json={
                        'message': message,
                        'session_id': session_id,
                        'actor_id': args.patient,
                        'identity_verified': args.verified,
                    },
                    timeout=args.timeout,
                )
            if resp.status_code == 409:
                # The gateway refuses to hand one patient's session to another.
                print(f'{RED}  {resp.json().get("detail", "session conflict")}{RESET}\n')
                continue
            resp.raise_for_status()
            data = resp.json()
        except KeyboardInterrupt:
            # Interrupting only abandons the response; the turn has already been
            # gated and audited server-side. Say so, rather than tearing down
            # the session over what is usually just impatience with CPU
            # inference.
            print(f'\n{YELLOW}  cancelled -- the turn may still have completed '
                  f'on the server (check /audit){RESET}\n')
            continue
        except Exception as e:
            print(f'{RED}request failed: {e}{RESET}\n')
            continue

        for call in data.get('tool_calls', []):
            status = ''
            if call.get('malformed'):
                status = f' {YELLOW}(written as text -- not executed){RESET}'
            elif call.get('executed'):
                status = f' {GREEN}(executed){RESET}'
            elif call.get('blocked_by'):
                status = f' {RED}(blocked: {", ".join(call["blocked_by"])}){RESET}'
            print(
                f'{BLUE}  [tool] {call["name"]}({json.dumps(call["arguments"])}){RESET}{status}'
            )

        render_decisions(data.get('decisions', []))
        render_flow(data.get('flow', []), verbose=args.verbose)

        if data.get('escalated'):
            reasons = ', '.join(data.get('escalation_reasons', []))
            print(f'{MAGENTA}  [escalated] {reasons}{RESET}')

        # Only claim substitution when the reply itself was replaced. A denied
        # tool call followed by the model explaining itself is the healthy case,
        # and labelling that "substituted" credits the gate with words it did
        # not write.
        if data.get('reply_substituted'):
            marker = f'{RED}agent (guardrail substituted) >{RESET}'
        elif data.get('blocked'):
            marker = f'{YELLOW}agent (tool blocked, own words) >{RESET}'
        else:
            marker = f'{BOLD}agent >{RESET}'
        print(f'{marker} {data["reply"]}\n')


if __name__ == '__main__':
    sys.exit(main())
