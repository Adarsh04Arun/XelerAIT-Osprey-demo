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
    on-call clinician, depending on the reason."""

    def __init__(self, maxlen: int = 200) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    # Three bands, not two. Self-harm sits above medical urgency: both need a
    # human, but one of them needs the human *now* and must never be read as
    # one alert among many.
    CRITICAL_REASONS = ('self_harm', 'self_harm_mishandled')

    @classmethod
    def _priority(cls, reasons: List[str]) -> str:
        if any(r in cls.CRITICAL_REASONS for r in reasons):
            return 'critical'
        if any(r.startswith('urgent') or r == 'phi_boundary' for r in reasons):
            return 'high'
        return 'normal'

    def add(self, item: Dict[str, Any]) -> None:
        item['raised_at'] = datetime.now().isoformat(timespec='seconds')
        item['priority'] = self._priority(item.get('reasons', []))
        with self._lock:
            self._items.append(item)

        log = logger.critical if item['priority'] == 'critical' else logger.warning
        log(
            'ESCALATION [%s] session=%s reasons=%s',
            item['priority'], item.get('session_id'), item.get('reasons'),
        )

    _RANK = {'critical': 0, 'high': 1, 'normal': 2}

    def items(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            ordered = sorted(
                self._items,
                key=lambda i: (self._RANK.get(i.get('priority', 'normal'), 2), i.get('raised_at', '')),
            )
        return ordered[:limit]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


audit_log = AuditLog()
escalations = EscalationQueue()


# ---------------------------------------------------------------------------
# Entry point -- called once per turn, after all decisions are in
# ---------------------------------------------------------------------------


def apply_effects(turn: Any, user_message: str) -> None:
    """Write the audit trail and raise escalations for one completed turn."""
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
            }
        )
