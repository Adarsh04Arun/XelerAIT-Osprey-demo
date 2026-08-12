"""Pre-processing enrichment -- the direct analogue of osprey-atproto/enricher.

In osprey-atproto this is a Go service that consumes raw firehose events and
fans out to Ozone, the Appview, PLC, Hive, Abyss and Milvus, folding every
response into the event JSON before Osprey ever sees it.

Here it is a function, because the 'external services' are a dict in memory.
The contract is what matters and it is identical:

    every fact a rule needs is resolved HERE, before the event is produced

so that rules stay pure comparisons over JSON. A rule that had to call the
scheduling API itself would be slow, untestable, and impossible to reason about
when it fired. That constraint is the single most important thing to carry over
from the atproto design.

Adding a new guardrail therefore usually means adding a field here first, then
a variable in models/*.sml, then the rule.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Deque, Dict, Optional

from .config import settings
from .hospital import store


# ---------------------------------------------------------------------------
# Session activity store
#
# Stands in for the counters osprey-atproto keeps in memcached. The rules also
# maintain their own windows via the IncrementWindow UDF; these are the
# gateway-side view, used for facts the rules want as plain numbers.
# ---------------------------------------------------------------------------


class SessionActivity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Dict[str, Deque[tuple]] = defaultdict(lambda: deque(maxlen=200))

    def record(self, session_id: str, kind: str) -> None:
        with self._lock:
            self._events[session_id].append((time.monotonic(), kind))

    def count(self, session_id: str, kind: str, window_seconds: float) -> int:
        cutoff = time.monotonic() - window_seconds
        with self._lock:
            return sum(1 for ts, k in self._events[session_id] if k == kind and ts >= cutoff)

    def book_cancel_cycles(self, session_id: str) -> int:
        """How many times this session booked then cancelled. Cheap churn proxy."""
        with self._lock:
            kinds = [k for _, k in self._events[session_id]]
        cycles = 0
        for i in range(len(kinds) - 1):
            if kinds[i] == 'book' and kinds[i + 1] == 'cancel':
                cycles += 1
        return cycles

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


sessions = SessionActivity()


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _parse_start(raw: Optional[str]) -> Optional[datetime]:
    """Parse the agent's proposed start time.

    Models produce all sorts of shapes here. Anything unparseable returns None,
    which the slot enrichment reports as 'does not exist' -- and the ruleset
    denies. Failing closed on a malformed time is correct: we would rather ask
    the patient again than book a guess.
    """
    if not raw:
        return None
    text = str(raw).strip().replace(' ', 'T')
    try:
        # Handles "2026-08-14T09:30" and "2026-08-14T09:30:00" alike.
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def enrich_slot(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = tool_args.get('provider_id') or ''
    raw_start = tool_args.get('start')
    when = _parse_start(raw_start)

    if when is None:
        return {
            'exists': False,
            'is_taken': False,
            'taken_by': None,
            'minutes_until_start': 0.0,
            'within_clinic_hours': False,
            'weekday': None,
            'parse_ok': False,
        }

    canonical = when.strftime('%Y-%m-%dT%H:%M')
    occupant = store.slot_occupant(provider_id, canonical)
    within_hours = store.is_within_clinic_hours(when)
    on_grid = store.is_valid_slot_start(when)

    return {
        'exists': on_grid and within_hours,
        'is_taken': occupant is not None,
        'taken_by': occupant,
        'minutes_until_start': (when - datetime.now()).total_seconds() / 60.0,
        'within_clinic_hours': within_hours,
        'weekday': when.strftime('%A'),
        'canonical_start': canonical,
        'parse_ok': True,
    }


def enrich_provider(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = tool_args.get('provider_id') or ''
    provider = store.providers.get(provider_id)

    if provider is None:
        return {
            'exists': False,
            'is_available': False,
            'daily_cap': 0,
            'booked_on_requested_day': 0,
            'department': None,
        }

    when = _parse_start(tool_args.get('start'))
    date_str = when.date().isoformat() if when else ''

    return {
        'exists': True,
        'is_available': store.provider_available_on(provider, when) if when else True,
        'daily_cap': provider.daily_cap,
        'booked_on_requested_day': store.provider_booked_on(provider_id, date_str) if date_str else 0,
        'department': provider.department,
        'name': provider.name,
    }


def enrich_appointment(tool_args: Dict[str, Any], actor_id: Optional[str]) -> Dict[str, Any]:
    appointment_id = tool_args.get('appointment_id') or ''
    apt = store.appointments.get(appointment_id)

    if apt is None:
        return {'exists': False, 'belongs_to_actor': False, 'status': None}

    return {
        'exists': True,
        # The ownership check that stops one patient moving another's booking.
        'belongs_to_actor': bool(actor_id) and apt.patient_id == actor_id,
        'status': apt.status,
        'owner_id': apt.patient_id,
        'provider_id': apt.provider_id,
        'start': apt.start,
    }


def enrich_patient(
    tool_args: Dict[str, Any],
    actor_id: Optional[str],
    model_patient_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Facts about the patient this call targets.

    `model_patient_id` is what the MODEL asked for, before the gateway replaced
    it with the session identity. It matters because the replacement happens
    upstream of enrichment, so without carrying the original here the ruleset
    can only ever see the corrected id -- and an attempt to act on another
    patient becomes invisible to every rule written to catch exactly that.
    """
    target_id = tool_args.get('patient_id') or actor_id
    patient = store.patients.get(target_id) if target_id else None

    duplicate = store.find_patient_by_identity(
        tool_args.get('full_name') or '',
        tool_args.get('date_of_birth') or '',
    )

    return {
        'exists': patient is not None,
        'duplicate_found': duplicate is not None,
        'duplicate_id': duplicate.id if duplicate else None,
        # Is the chart being written the same person the gateway verified?
        'target_is_verified_actor': bool(actor_id) and target_id == actor_id,
        'target_id': target_id,
        # What the model actually asked for, and whether the gateway had to
        # overrule it. Surfaced so a rule can see the intent even though the
        # action itself was already made safe.
        'model_supplied_id': model_patient_id or '',
        'identity_substituted': bool(
            model_patient_id and actor_id and model_patient_id != actor_id
        ),
    }


def call_fingerprint(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """A stable short hash of "this exact call with these exact arguments".

    Rules cannot hash anything themselves -- they are pure comparisons over
    JSON -- so the fingerprint is resolved here like every other fact, and
    rules/duplicate_call.sml just counts how often the same one recurs.

    Sorted keys so argument ordering from the model does not change the hash;
    if it did, a model retrying the identical call with its JSON keys in a
    different order would look like a brand new attempt, which is precisely the
    case the breaker exists to catch.
    """
    if not tool_name:
        return ''
    payload = json.dumps({'t': tool_name, 'a': tool_args or {}}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


# Strings models emit when they mean "I don't have a value". They arrive as
# real strings, not JSON null, so nothing downstream treats them as missing:
# a provider_id of "null" is looked up, not found, and denied as a hallucinated
# clinician -- producing the rule text "Provider null does not exist", which is
# true, useless, and hides the actual problem.
_NULL_LIKE = {'', 'null', 'none', 'nil', 'nan', 'undefined', 'n/a', 'na',
              'string', 'unknown', '<unknown>', '...'}


def _is_null_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _NULL_LIKE
    return False


def enrich_call_shape(tool_name: str, tool_args: Dict[str, Any], actor_id: Optional[str]) -> Dict[str, Any]:
    """Is this tool call substantive, or did the model fill every field with a
    placeholder?

    A real observed call:

        book_appointment(provider_id="null", start="null", department="null")

    Every downstream rule fires on that -- unknown provider, unparseable start,
    outside clinic hours -- and all three are technically correct and
    collectively unhelpful. Three denials describing the same non-event bury
    the finding, which is that the model proposed a booking without having
    decided anything about it.

    patient_id is excluded from the count because the gateway injects it from
    the session, so it is always populated and would make every call look like
    it carried at least one real argument.
    """
    considered = {k: v for k, v in (tool_args or {}).items() if k != 'patient_id'}
    null_like = sorted(k for k, v in considered.items() if _is_null_like(v))

    return {
        'arg_count': len(considered),
        'null_like_args': null_like,
        'null_like_count': len(null_like),
        # False only when the model supplied arguments and every one of them was
        # a placeholder. A tool that legitimately takes no arguments (or whose
        # only argument is the injected patient_id) is not malformed.
        'has_usable_args': len(considered) == 0 or len(null_like) < len(considered),
    }


def enrich_session(session_id: str) -> Dict[str, Any]:
    return {
        'bookings_last_5m': sessions.count(session_id, 'book', 5 * 60),
        'cancels_last_10m': sessions.count(session_id, 'cancel', 10 * 60),
        'onboards_last_10m': sessions.count(session_id, 'onboard', 10 * 60),
        'book_cancel_cycles': sessions.book_cancel_cycles(session_id),
    }


def enrich(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    session_id: str,
    actor_id: Optional[str],
    model_patient_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the `enrichment` block for one proposed action.

    Only fetches what the tool actually needs -- the same 'minimum parsing per
    event' principle rules_atproto applies with its `require_if` guards.
    """
    enrichment: Dict[str, Any] = {
        'session': enrich_session(session_id),
        # Identity of this exact call, for the repeat-call breaker. Cheap enough
        # to compute unconditionally, and computing it only for some tools would
        # be the same enumerate-what-to-protect mistake that left booking
        # ungated.
        'call': dict(
            enrich_call_shape(tool_name, tool_args, actor_id),
            fingerprint=call_fingerprint(tool_name, tool_args),
        ),
        'clinic': {
            'open_hour': settings.clinic_open_hour,
            'close_hour': settings.clinic_close_hour,
            'slot_minutes': settings.slot_minutes,
            'now': datetime.now().strftime('%Y-%m-%dT%H:%M'),
        },
    }

    if tool_name in ('book_appointment', 'reschedule_appointment'):
        enrichment['slot'] = enrich_slot(tool_args)
        enrichment['provider'] = enrich_provider(tool_args)

    if tool_name in ('reschedule_appointment', 'cancel_appointment', 'lookup_appointments'):
        enrichment['appointment'] = enrich_appointment(tool_args, actor_id)

    # Every tool that names a patient, with no list to fall out of.
    #
    # This was an explicit tuple of four tool names, and lookup_appointments was
    # missing from it -- so TargetIsVerifiedActor was never computed for a read,
    # and pat-1001 could ask for pat-1002's appointments with no rule firing at
    # all. The gateway's identity injection hid it in the /chat path, which is
    # exactly why it survived: the hole was real and unreachable at the same
    # time, so nothing failed.
    #
    # That is the second time this shape of bug has landed here. The first is
    # written up in the README ("Booking was never identity-gated"), with the
    # lesson "a guard that enumerates operations will eventually miss one" --
    # and the fix at the time was to add booking to the list, which left the
    # list. So: no list. If a tool takes a patient_id it gets patient
    # enrichment, decided by the argument rather than by memory.
    if 'patient_id' in (tool_args or {}) or tool_name in ('onboard_patient',):
        enrichment['patient'] = enrich_patient(tool_args, actor_id, model_patient_id)

    return enrichment
