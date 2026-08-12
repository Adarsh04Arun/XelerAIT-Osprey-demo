"""Unit tests for the effector. No stack required -- these import the module
directly, so they run in milliseconds and fail on logic rather than on Docker.

Everything else in tests/ drives HTTP because the thing under test is a ruleset
running in another process. These do not, because the things under test here
are ordinary Python data structures whose bugs are invisible over HTTP: an
escalation silently dropped on the way into a bounded queue looks, from the
outside, exactly like an escalation that was never raised.
"""

from __future__ import annotations

import pytest

from gateway.app.effector import AuditLog, EscalationQueue, _redact_args, redact
from gateway.app.fallbacks import (
    BLOCKED_FALLBACK,
    SELF_HARM_FALLBACK,
    URGENT_FALLBACK,
    fallback_for,
)


# ---------------------------------------------------------------------------
# Blocked-reply wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'code',
    [
        'out_of_scope',
        'clinical_advice',
        'clinical_advice_request',
        'medication_advice',
        'cross_patient_access',
        'identity_not_verified',
        'malformed_tool_call',
        'booking_rate_limit',
        'read_rate_limit',
        'verdict_timeout',
        'some_rule_added_next_year',
    ],
)
def test_every_blocked_reply_is_identical(code):
    """One wording for every block, so a refusal is not a probe oracle.

    Per-category refusals let an adversary read which rule caught them, adjust,
    and retry. Answering identically means a blocked turn reveals only that it
    was blocked. The unknown code is in the list on purpose: a rule added later
    must inherit this by default, not by someone remembering to.
    """
    assert fallback_for([code]) == BLOCKED_FALLBACK
    assert 'cannot be processed' in fallback_for([code]).lower()


def test_self_harm_is_the_exception():
    """The one place a uniform refusal would be dangerous.

    Here the replacement TEXT is the safety control, not the block. Someone who
    says "I want to die" needs Samaritans and emergency services; "that request
    cannot be processed" is a door closing on a person in crisis, and it is the
    last thing they hear from us. Collapsing this into the generic message would
    remove the control while leaving every appearance of it -- the rule still
    fires, the audit still records a save, and nobody is helped.
    """
    reply = fallback_for(['self_harm_disclosure'])
    assert reply == SELF_HARM_FALLBACK
    assert reply != BLOCKED_FALLBACK
    assert '116 123' in reply, 'crisis line missing'
    assert 'cannot be processed' not in reply.lower()


def test_urgent_symptom_is_the_other_exception():
    """"Cannot be processed" reads as a system fault, and the plausible response
    to a system fault is to wait and try again. Not what you want from someone
    describing chest pain."""
    reply = fallback_for(['urgent_symptom'])
    assert reply == URGENT_FALLBACK
    assert 'emergency services' in reply.lower()
    assert 'cannot be processed' not in reply.lower()


def test_crisis_outranks_a_generic_block_on_the_same_turn():
    """One turn can trip several rules; the patient sees one reply."""
    assert fallback_for(['out_of_scope', 'self_harm_disclosure']) == SELF_HARM_FALLBACK
    assert fallback_for(['clinical_advice', 'urgent_symptom']) == URGENT_FALLBACK
    assert fallback_for(['self_harm_disclosure', 'urgent_symptom']) == SELF_HARM_FALLBACK


# ---------------------------------------------------------------------------
# Escalation retention
# ---------------------------------------------------------------------------


def test_critical_escalations_survive_queue_overflow():
    """Regression: the critical band was defeated by the queue's own eviction.

    Escalations were held in a single `deque(maxlen=200)`, which evicts
    oldest-first and knows nothing about priority. A self-harm escalation could
    therefore be pushed out by two hundred subsequent rate-limit alerts --
    silently, with no log line and no counter.

    That undid the entire point of the band. Ranking on read does not help if
    the item is gone before anyone reads.
    """
    q = EscalationQueue(maxlen=3)
    q.add({'session_id': 'crisis', 'reasons': ['self_harm']})

    for i in range(50):
        q.add({'session_id': f'noise-{i}', 'reasons': ['booking_flood']})

    sessions = [i['session_id'] for i in q.items(100)]
    assert 'crisis' in sessions, 'a critical escalation was evicted by routine noise'
    assert q.items(100)[0]['priority'] == 'critical', 'critical must sort first'


def test_dropped_non_critical_escalations_are_counted():
    """Overflow is acceptable; overflow nobody can detect is not."""
    q = EscalationQueue(maxlen=2)
    for i in range(6):
        q.add({'session_id': f's{i}', 'reasons': ['cancel_churn']})

    assert q.dropped() == 4, f'expected 4 dropped, counted {q.dropped()}'


def test_priority_bands():
    q = EscalationQueue()
    q.add({'session_id': 'a', 'reasons': ['self_harm_mishandled']})
    q.add({'session_id': 'b', 'reasons': ['phi_boundary']})
    q.add({'session_id': 'c', 'reasons': ['urgent_triage']})
    q.add({'session_id': 'd', 'reasons': ['cancel_churn']})

    by_session = {i['session_id']: i['priority'] for i in q.items(10)}
    assert by_session == {'a': 'critical', 'b': 'high', 'c': 'high', 'd': 'normal'}


def test_clear_resets_every_band():
    q = EscalationQueue(maxlen=1)
    q.add({'session_id': 'crit', 'reasons': ['self_harm']})
    q.add({'session_id': 'x', 'reasons': ['cancel_churn']})
    q.add({'session_id': 'y', 'reasons': ['cancel_churn']})
    q.clear()

    assert q.items(10) == []
    assert q.dropped() == 0


# ---------------------------------------------------------------------------
# PHI redaction
# ---------------------------------------------------------------------------


def test_redaction_removes_direct_identifiers():
    text = 'Call me on +44 7700 900123 or rosa.iyer@example.com, DOB 1984-03-11, INS-99812'
    out = redact(text)

    for leaked in ('7700', '@example.com', '1984-03-11', 'INS-99812'):
        assert leaked not in out, f'{leaked!r} survived redaction: {out!r}'


def test_redaction_keeps_identifiers_needed_for_audit():
    """An audit trail without patient_id cannot be reconstructed. Contact
    details go; the keys that let you find the record stay."""
    args = _redact_args(
        {
            'patient_id': 'pat-1001',
            'appointment_id': 'apt-5001',
            'phone': '+44 7700 900123',
            'date_of_birth': '1984-03-11',
            'notes': 'reachable on 07700 900123',
        }
    )

    assert args['patient_id'] == 'pat-1001'
    assert args['appointment_id'] == 'apt-5001'
    assert args['phone'] == '[redacted]'
    assert args['date_of_birth'] == '[redacted]'
    assert '900123' not in args['notes'], 'free text was not scrubbed'


def test_audit_log_is_bounded_and_ordered():
    log = AuditLog(maxlen=3)
    for i in range(5):
        log.record({'n': i})

    entries = log.entries(10)
    assert [e['n'] for e in entries] == [2, 3, 4]
    assert all('recorded_at' in e for e in entries)
