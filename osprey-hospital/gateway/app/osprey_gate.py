"""Synchronous policy gate over the asynchronous Osprey worker.

Osprey is a stream engine: events go in one topic, results come out another,
and nothing is waiting. osprey-atproto embraces that -- its effector applies
labels and takedowns whenever results happen to arrive.

A hospital agent needs the opposite. "That booking was invalid" is worthless
after the patient has been told it's confirmed. So this module wraps the async
engine in request/reply:

    1. stamp the proposed action with a unique action_id
    2. produce it to osprey.actions_input  (Osprey's built-in Kafka input
       stream reads plain JSON -- no custom input stream plugin needed)
    3. block on a Future keyed by that action_id
    4. a background consumer reads hospital.verdicts and resolves it
       (published by plugins_hospital VerdictKafkaSink)

Osprey itself is unmodified and still just runs rules.

Failure behaviour is the important part: if the verdict does not arrive, the
gate FAILS CLOSED by default. A guardrail pipeline that is down must stop the
agent, not silently wave it through.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kafka import KafkaConsumer, KafkaProducer

from .config import settings

logger = logging.getLogger('hospital.gate')


# ---------------------------------------------------------------------------
# Verdict model
# ---------------------------------------------------------------------------


# How denial reasons are ordered when they are read back to the model.
#
# Presentation only -- it changes nothing about what is denied, and every
# verdict is still reported in full. Rules stay a flat set of independent
# comparisons with no ordering between them, which is what makes them
# individually testable; the ranking lives out here because it is a question
# about explaining a decision, not about making one.
#
# Anything unlisted sits at 50, between the root causes and the details.
_DENIAL_RANK = {
    # Shape problems first: if the call was empty, nothing else is the reason.
    'deny:malformed_tool_call': 0,
    'deny:repeated_identical_call': 1,
    # Then identity and permission, which no amount of retrying will fix.
    'deny:identity_not_verified': 10,
    'deny:cross_patient_access': 11,
    'deny:cross_patient_booking': 11,
    'deny:cross_patient_write': 11,
    'deny:out_of_scope': 12,
    # Then the specific, correctable scheduling facts.
    'deny:unparseable_start': 60,
    'deny:outside_clinic_hours': 61,
    'deny:slot_does_not_exist': 62,
    'deny:unknown_provider': 63,
}


@dataclass
class Decision:
    """The outcome of running one proposed action through the ruleset."""

    allowed: bool
    verdicts: List[str] = field(default_factory=list)
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    escalations: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    timed_out: bool = False
    latency_ms: float = 0.0

    @property
    def denial_reasons(self) -> List[str]:
        """Human-readable rule descriptions behind each denial. This is what the
        agent is told, so it can explain itself to the patient rather than just
        failing.

        Deduplicated: one rule that emits both a `deny:` and an `escalate:`
        verdict appears once per verdict, and repeating the same sentence back
        to the model just makes the correction noisier.

        Ordered so the root cause leads. A call with placeholder arguments trips
        every downstream rule at once -- unknown provider, unparseable time,
        outside clinic hours -- and each is true while none names the actual
        problem. Handing the model three contradictory corrections invites it to
        fix one and resubmit; handing it "you filled every field with null"
        first gives it something it can act on.
        """
        seen = [
            (str(r.get('verdict', '')), r['description'])
            for r in self.reasons
            if str(r.get('verdict', '')).startswith('deny:')
        ]
        deduped = list(dict.fromkeys(seen))
        ordered = sorted(deduped, key=lambda vd: _DENIAL_RANK.get(vd[0], 50))
        return [description for _, description in ordered]

    @property
    def denial_codes(self) -> List[str]:
        return [v.split(':', 1)[1] for v in self.verdicts if v.startswith('deny:')]


def _classify(verdicts: List[str]) -> Dict[str, List[str]]:
    """Split verdict strings by their prefix.

    The prefix convention (`deny:`, `escalate:`, `flag:`, `observe:`) lives in
    the SML, not in code -- adding a new outcome type means writing a rule, not
    editing the gateway.
    """
    out: Dict[str, List[str]] = {'deny': [], 'escalate': [], 'flag': [], 'observe': []}
    for v in verdicts:
        prefix, _, rest = v.partition(':')
        if prefix in out:
            out[prefix].append(rest or v)
    return out


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class OspreyGate:
    def __init__(self) -> None:
        self._pending: Dict[str, Future] = {}
        self._lock = threading.Lock()
        self._action_seq = int(time.time() * 1000)
        self._producer: Optional[KafkaProducer] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._running = False

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            linger_ms=0,
            acks=1,
            retries=3,
            max_block_ms=10000,
        )
        self._running = True
        self._consumer_thread = threading.Thread(
            target=self._consume_verdicts, name='verdict-consumer', daemon=True
        )
        self._consumer_thread.start()
        logger.info(
            'osprey gate started (actions=%s verdicts=%s)',
            settings.actions_topic,
            settings.verdict_topic,
        )

    def stop(self) -> None:
        self._running = False
        if self._producer:
            try:
                self._producer.flush(timeout=5)
                self._producer.close(timeout=5)
            except Exception:
                logger.warning('error closing producer', exc_info=True)

    # -- verdict consumer ---------------------------------------------------

    def _consume_verdicts(self) -> None:
        consumer = None
        while self._running:
            try:
                if consumer is None:
                    consumer = KafkaConsumer(
                        settings.verdict_topic,
                        bootstrap_servers=settings.kafka_bootstrap_servers,
                        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                        auto_offset_reset='latest',
                        enable_auto_commit=True,
                        group_id=None,  # every gateway instance sees every verdict
                        consumer_timeout_ms=1000,
                    )
                    logger.info('verdict consumer connected')

                for record in consumer:
                    if not self._running:
                        break
                    self._resolve(record.value)

            except StopIteration:
                continue  # consumer_timeout_ms elapsed with no messages; normal
            except Exception:
                logger.warning('verdict consumer error, reconnecting', exc_info=True)
                consumer = None
                time.sleep(2)

    def _resolve(self, payload: Dict[str, Any]) -> None:
        action_id = str(payload.get('action_id', ''))
        with self._lock:
            future = self._pending.pop(action_id, None)
        if future is None:
            # Late arrival for something we already timed out on, or a verdict
            # for a different gateway instance. Nothing to do.
            return
        if not future.done():
            future.set_result(payload)

    # -- the gate itself ----------------------------------------------------

    def _next_action_id(self) -> str:
        with self._lock:
            self._action_seq += 1
            return str(self._action_seq)

    def evaluate(
        self,
        *,
        request_id: str,
        session_id: str,
        actor_id: Optional[str],
        actor_kind: str,
        identity_verified: bool,
        tool_name: str,
        tool_args: Dict[str, Any],
        user_message: str,
        agent_reply: str,
        enrichment: Dict[str, Any],
    ) -> Decision:
        """Run one proposed action past the ruleset and block for the verdict."""
        started = time.monotonic()
        action_id = self._next_action_id()
        future: Future = Future()

        with self._lock:
            self._pending[action_id] = future

        # Osprey's built-in Kafka input stream expects exactly this envelope:
        #   {"send_time": <rfc3339>, "data": {"action_id", "action_name", "data"}}
        envelope = {
            'send_time': datetime.now(timezone.utc).isoformat(),
            'data': {
                'action_id': action_id,
                'action_name': 'agent_tool_call' if tool_name else 'agent_message',
                'data': {
                    'request_id': request_id,
                    'session_id': session_id,
                    'actor_id': actor_id or '',
                    'actor_kind': actor_kind,
                    'identity_verified': identity_verified,
                    'tool_name': tool_name or '',
                    'tool_args': tool_args or {},
                    'user_message': user_message or '',
                    'agent_reply': agent_reply or '',
                    'enrichment': enrichment or {},
                },
            },
        }

        try:
            assert self._producer is not None, 'gate not started'
            self._producer.send(settings.actions_topic, value=envelope)
            self._producer.flush(timeout=5)
        except Exception:
            logger.exception('failed to produce action %s to osprey', action_id)
            with self._lock:
                self._pending.pop(action_id, None)
            return self._fail(started, 'produce_failed')

        try:
            payload = future.result(timeout=settings.verdict_timeout_seconds)
        except Exception:
            with self._lock:
                self._pending.pop(action_id, None)
            logger.error('no verdict for action %s within %ss', action_id, settings.verdict_timeout_seconds)
            return self._fail(started, 'verdict_timeout', timed_out=True)

        verdicts = list(payload.get('verdicts') or [])
        buckets = _classify(verdicts)

        return Decision(
            allowed=not buckets['deny'],
            verdicts=verdicts,
            reasons=list(payload.get('reasons') or []),
            escalations=buckets['escalate'],
            flags=buckets['flag'],
            latency_ms=(time.monotonic() - started) * 1000,
        )

    def _fail(self, started: float, code: str, timed_out: bool = False) -> Decision:
        """Fail closed unless explicitly configured otherwise."""
        allowed = settings.fail_open
        return Decision(
            allowed=allowed,
            verdicts=[] if allowed else [f'deny:{code}'],
            reasons=[]
            if allowed
            else [
                {
                    'verdict': f'deny:{code}',
                    'rule': 'GatewayFailClosed',
                    'description': (
                        'The guardrail service did not respond, so the action was blocked. '
                        'Please try again or ask a member of staff.'
                    ),
                }
            ],
            timed_out=timed_out,
            latency_ms=(time.monotonic() - started) * 1000,
        )


gate = OspreyGate()
