"""Post-processing actions -- the analogue of osprey-atproto/effector.

In osprey-atproto the effector is a Go service consuming result events and
calling Ozone to apply labels, tags, takedowns and reports, plus BigQuery for
analytics and Slack for alerts.

Here the effects are the operational equivalents: an audit trail, and a queue
of turns a human needs to look at. The structural point carries over exactly --
side effects happen HERE, downstream of the decision, never inside a rule.

For the MVP these are in-memory and exposed over HTTP so you can watch them
fill up while you poke at the agent. Swap for a real audit store and a paging
integration and the call sites do not change.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List

from .provenance import stamp

logger = logging.getLogger('hospital.effector')


# ---------------------------------------------------------------------------
# PHI redaction
#
# The audit log is the one place guaranteed to see every message a patient
# sent, so it is the one place most likely to leak. Redact on the way in --
# a log you have to remember to sanitise later is a log that leaks.
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r'(\+?\d[\d\s().-]{7,}\d)')
_EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')
_DOB_RE = re.compile(r'\b(19|20)\d{2}-\d{2}-\d{2}\b')
_INSURANCE_RE = re.compile(r'\bINS-\d+\b', re.IGNORECASE)


def redact(text: str) -> str:
    if not text:
        return ''
    out = _EMAIL_RE.sub('[email]', text)
    out = _PHONE_RE.sub('[phone]', out)
    out = _DOB_RE.sub('[dob]', out)
    out = _INSURANCE_RE.sub('[insurance-id]', out)
    return out


def _redact_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Redact tool arguments. Identifiers are kept -- an audit trail without
    patient_id is useless -- but free text and direct contact details go."""
    redacted: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key in ('phone', 'email', 'date_of_birth', 'insurance_id'):
            redacted[key] = '[redacted]'
        elif isinstance(value, str):
            redacted[key] = redact(value)
        else:
            redacted[key] = value
    return redacted


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


class AuditLog:
    def __init__(self, maxlen: int = 1000) -> None:
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, entry: Dict[str, Any]) -> None:
        entry['recorded_at'] = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self._entries.append(entry)

    def entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class EscalationQueue:
    """Turns that need a human. In production this pages the front desk or the
    on-call clinician, depending on the reason.

    Bounded, but not uniformly. `deque(maxlen=...)` evicts oldest-first and
    ignores priority, which meant a self-harm escalation could be pushed out by
    two hundred subsequent rate-limit alerts -- silently, with no log line and
    no counter. The band exists precisely so that alert cannot queue behind
    routine noise; letting the queue's eviction policy undo that at the far end
    was the same bug wearing a different hat.

    So `critical` is held in its own unbounded deque. If a hospital ever
    generates enough suicidal disclosures to exhaust memory here, running out
    of memory is the correct and least-bad outcome.
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._critical: Deque[Dict[str, Any]] = deque()
        self._dropped = 0
        self._lock = threading.Lock()

    # Three bands, not two. Self-harm sits above medical urgency: both need a
    # human, but one of them needs the human *now* and must never be read as
    # one alert among many.
    CRITICAL_REASONS = ('self_harm', 'self_harm_mishandled')

    # Someone probing whether the controls are real belongs with PHI events:
    # below a crisis, above routine noise. The attempt failing does not make it
    # uninteresting -- what it tells you is about the caller, not the turn.
    HIGH_REASONS = ('phi_boundary', 'injection_attempt', 'injection_mishandled')

    @classmethod
    def _priority(cls, reasons: List[str]) -> str:
        if any(r in cls.CRITICAL_REASONS for r in reasons):
            return 'critical'
        if any(r.startswith('urgent') or r in cls.HIGH_REASONS for r in reasons):
            return 'high'
        return 'normal'

    def add(self, item: Dict[str, Any]) -> None:
        item['raised_at'] = datetime.now().isoformat(timespec='seconds')
        item['priority'] = self._priority(item.get('reasons', []))

        with self._lock:
            if item['priority'] == 'critical':
                self._critical.append(item)
            else:
                # Count what falls off the end. An escalation queue that loses
                # items without saying so is worse than one that overflows
                # loudly -- the whole point is that a human sees these.
                if len(self._items) == self._items.maxlen:
                    self._dropped += 1
                    logger.error(
                        'escalation queue full (%s) -- dropped the oldest non-critical '
                        'item; %s dropped in total',
                        self._items.maxlen, self._dropped,
                    )
                self._items.append(item)

        log = logger.critical if item['priority'] == 'critical' else logger.warning
        log(
            'ESCALATION [%s] session=%s reasons=%s',
            item['priority'], item.get('session_id'), item.get('reasons'),
        )

    _RANK = {'critical': 0, 'high': 1, 'normal': 2}

    def items(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            everything = list(self._critical) + list(self._items)
            ordered = sorted(
                everything,
                key=lambda i: (self._RANK.get(i.get('priority', 'normal'), 2), i.get('raised_at', '')),
            )
        return ordered[:limit]

    def dropped(self) -> int:
        """Non-critical items lost to overflow. Surfaced by /escalations so the
        queue cannot quietly under-report what needs a human."""
        with self._lock:
            return self._dropped

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._critical.clear()
            self._dropped = 0


audit_log = AuditLog()
escalations = EscalationQueue()


# ---------------------------------------------------------------------------
# Entry point -- called once per turn, after all decisions are in
# ---------------------------------------------------------------------------


def apply_effects(turn: Any, user_message: str) -> None:
    """Write the audit trail and raise escalations for one completed turn."""
    # What produced these decisions. Without it an audit row records the verdict
    # but not the policy or the model that reached it, and six months later
    # nobody can say whether a decision was correct under the rules of the day.
    provenance = dict(stamp(), prompt_version=getattr(turn, 'prompt_version', ''))

    for decision in turn.decisions:
        audit_log.record(
            {
                'session_id': turn.session_id,
                'request_id': turn.request_id,
                'stage': decision['stage'],
                'tool': decision['tool'],
                'allowed': decision['allowed'],
                'verdicts': decision['verdicts'],
                'rules_fired': [r.get('rule') for r in decision.get('reasons', [])],
                'latency_ms': decision['latency_ms'],
                'timed_out': decision['timed_out'],
                **provenance,
            }
        )

    for call in turn.tool_calls:
        audit_log.record(
            {
                'session_id': turn.session_id,
                'request_id': turn.request_id,
                'stage': 'tool_proposed',
                'tool': call['name'],
                'arguments': _redact_args(call.get('arguments', {})),
                # Whether the proposal actually ran. A trail that logs what was
                # attempted without logging what executed cannot distinguish a
                # blocked booking from a completed one.
                'executed': call.get('executed', False),
                'blocked_by': call.get('blocked_by', []),
                **provenance,
            }
        )

    if turn.escalated:
        escalations.add(
            {
                'session_id': turn.session_id,
                'request_id': turn.request_id,
                'reasons': list(dict.fromkeys(turn.escalation_reasons)),
                'user_message': redact(user_message),
                'agent_reply': redact(turn.reply),
                'tools_attempted': [c['name'] for c in turn.tool_calls],
                # The rules in their own words, not just the reason codes.
                # A reviewer woken at 2am gets "phi_boundary" otherwise, and has
                # to go and read the ruleset to find out what that meant here.
                'evidence': _evidence(turn),
                **provenance,
            }
        )


def _evidence(turn: Any) -> List[Dict[str, Any]]:
    """Every rule that fired this turn, with the description it carries.

    Deduplicated on (rule, description): one rule emitting both a `deny:` and an
    `escalate:` verdict appears once per verdict in the decision payload, and
    repeating the same sentence twice in a review item just makes it longer.
    """
    seen: Dict[tuple, Dict[str, Any]] = {}
    for decision in turn.decisions:
        for reason in decision.get('reasons', []):
            key = (reason.get('rule'), reason.get('description'))
            if key not in seen:
                seen[key] = {
                    'stage': decision['stage'],
                    'rule': reason.get('rule'),
                    'verdict': reason.get('verdict'),
                    'description': redact(str(reason.get('description', ''))),
                }
    return list(seen.values())
