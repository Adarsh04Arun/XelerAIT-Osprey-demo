"""The operations agent: llama3.1 via Ollama, with tool calling.

The system prompt tells the model to stay operational. It will not always
listen -- that is the point. The prompt is the soft boundary; rules_hospital/
is the hard one. If the prompt were sufficient there would be nothing to test.

Turn structure:

    user message
      -> model proposes a tool call
      -> gate: enrich, run rules, allow/deny        <- guardrail
      -> execute if allowed, else feed the denial back to the model
      -> model produces a reply
      -> gate: run rules on the REPLY               <- guardrail
      -> reply is released, or replaced with a safe fallback

Checking the reply matters as much as checking the tool call. Most of the ways
this agent can hurt someone involve no tool call at all -- it just answers a
medical question.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .config import settings
from .enricher import enrich, sessions
from .osprey_gate import Decision, gate
from .tools import GATED_TOOLS, TOOL_SCHEMAS, WRITE_TOOLS, execute

# Every tool the model can name. Used to tell a real tool call written as prose
# apart from an unrelated JSON blob in the reply.
_EXECUTABLE_TOOL_NAMES = {t['function']['name'] for t in TOOL_SCHEMAS}

logger = logging.getLogger('hospital.agent')


SYSTEM_PROMPT = """You are the scheduling assistant for Ashgrove Family Practice.

Your job is to GET PEOPLE BOOKED IN. You are helpful and you complete tasks.

You handle:
- booking, rescheduling and cancelling appointments
- registering new patients
- updating contact and insurance details
- questions about opening hours, location and parking

There is exactly one thing you do not do: give medical advice. That means no
diagnosis, no medication or dosage guidance, no judging how serious a symptom
is, no interpreting test results, and no writing medical information onto a
patient record.

Critically: "I can't advise on that" is NEVER a complete answer on its own.
Not advising is not the same as not helping. Someone who mentions a symptom is
almost always trying to get an appointment, and booking them is your job.

  Patient: "I'm feeling feverish, I'd like to see a nurse."
  Good:    "I can't advise on symptoms, but I can absolutely get you booked in.
            Let me check what's available." -> then call find_open_slots
  Bad:     "I can't help with that."

  Patient: "I've had a headache for days, what should I take?"
  Good:    "I'm not able to advise on medication, but I can book you with a
            clinician who can. Would you like me to look for a slot?"
  Bad:     "Take two paracetamol."
  Bad:     "I can't help with that."

The only time you do NOT offer an appointment is when something sounds like an
emergency, or when someone describes wanting to harm themselves. Then you stop,
tell them to contact emergency services or the urgent care line right away, and
say you are alerting a member of staff. Do not offer to book. Do not continue
the conversation as though it were an admin task.

Practical rules:
- Call list_providers before you use a provider id. Never invent one.
- Call find_open_slots before booking. Never invent an appointment time.
- Use slot times exactly as returned by find_open_slots.
- Do not write tool calls as text in your reply. Use the tool-calling mechanism.
- Never ask the patient for their patient id; the system already knows it.
- Today's date is {today} ({weekday}). The clinic is open {open_hour}:00-{close_hour}:00, Monday to Friday.
- Be brief, warm and concrete."""


@dataclass
class Turn:
    """Everything that happened on one exchange -- returned to the caller so the
    guardrails are observable rather than invisible."""

    reply: str
    session_id: str
    request_id: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    escalation_reasons: List[str] = field(default_factory=list)
    blocked: bool = False


class Session:
    def __init__(self, session_id: str, actor_id: Optional[str], identity_verified: bool):
        self.id = session_id
        self.actor_id = actor_id
        self.identity_verified = identity_verified
        self.messages: List[Dict[str, Any]] = [{'role': 'system', 'content': self._system_prompt()}]

    @staticmethod
    def _system_prompt() -> str:
        now = datetime.now()
        return SYSTEM_PROMPT.format(
            today=now.strftime('%Y-%m-%d'),
            weekday=now.strftime('%A'),
            open_hour=settings.clinic_open_hour,
            close_hour=settings.clinic_close_hour,
        )


_SESSIONS: Dict[str, Session] = {}


def get_session(session_id: str, actor_id: Optional[str], identity_verified: bool) -> Session:
    session = _SESSIONS.get(session_id)
    if session is None:
        session = Session(session_id, actor_id, identity_verified)
        _SESSIONS[session_id] = session
    else:
        # Identity can be established mid-conversation.
        session.actor_id = actor_id or session.actor_id
        session.identity_verified = identity_verified or session.identity_verified
    return session


def reset_sessions() -> None:
    _SESSIONS.clear()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def _chat(messages: List[Dict[str, Any]], use_tools: bool = True) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'model': settings.ollama_model,
        'messages': messages,
        'stream': False,
        'options': {'temperature': settings.ollama_temperature},
    }
    if use_tools:
        payload['tools'] = TOOL_SCHEMAS

    resp = requests.post(
        f'{settings.ollama_host}/api/chat',
        json=payload,
        timeout=settings.ollama_timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json().get('message', {})


# ---------------------------------------------------------------------------
# Guardrail helpers
# ---------------------------------------------------------------------------


SAFE_FALLBACK = (
    "I'm not able to advise on anything medical. I can book you in with a "
    "clinician, or put you through to the care team if you'd like to speak to "
    "someone now."
)

URGENT_FALLBACK = (
    "That sounds like it needs attention right away, and it's not something I "
    "can help with as a scheduling assistant. Please call emergency services "
    "now, or our urgent care line. I'm flagging this to a member of staff."
)

# Deliberately does not offer to book anything, and does not ask questions.
# The failing behaviour this replaces was the model saying "I can't help with
# that" and then offering an appointment slot.
SELF_HARM_FALLBACK = (
    "I'm really glad you told me, and I want to make sure you get proper "
    "support right now rather than an appointment slot.\n\n"
    "Please contact one of these straight away:\n"
    "  - Emergency services, if you are in immediate danger\n"
    "  - Samaritans: 116 123 (free, 24 hours a day)\n"
    "  - Text SHOUT to 85258\n\n"
    "I'm alerting a member of our staff now so someone can reach out to you. "
    "You don't have to deal with this on your own."
)


_TOOL_NAME_HINT = re.compile(r'"name"\s*:\s*"([a-z_]+)"')


def _find_json_objects(text: str) -> List[tuple]:
    """Locate balanced {...} spans in text. Returns (start, end, parsed) tuples.

    Brace counting rather than a regex, because tool-call payloads nest objects
    and a regex can't match balanced delimiters.
    """
    out = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                blob = text[start : i + 1]
                try:
                    out.append((start, i + 1, json.loads(blob)))
                except ValueError:
                    pass
                start = -1
    return out


def extract_text_tool_calls(content: str) -> tuple:
    """Pull tool calls that the model wrote as prose instead of using the API.

    llama3.1 intermittently emits `{"name": "book_appointment", "parameters":
    {...}}` as message *content* rather than in the structured `tool_calls`
    field. Two problems with letting that through:

      1. the patient sees raw JSON in their reply
      2. it never reaches the tool gate, so the model's intent is invisible --
         in one session it proposed a booking this way immediately after a
         self-harm disclosure, and the tool-call rules never saw it

    Returns (cleaned_text, calls). The calls are gated but NOT executed: acting
    on a malformed tool call from a confused model is not a capability worth
    having, and the model is told to retry properly.
    """
    if not content or '{' not in content:
        return content, []

    calls = []
    spans = []
    for start, end, obj in _find_json_objects(content):
        if not isinstance(obj, dict):
            continue
        name = obj.get('name')
        args = obj.get('parameters', obj.get('arguments'))
        if isinstance(name, str) and name in _EXECUTABLE_TOOL_NAMES and isinstance(args, dict):
            calls.append({'name': name, 'arguments': args})
            spans.append((start, end))

    if not calls:
        # A JSON-ish blob that isn't a tool call. If it merely looks like one,
        # still strip it rather than show the patient braces.
        if _TOOL_NAME_HINT.search(content):
            return re.sub(r'\{.*\}', '', content, flags=re.DOTALL).strip(), []
        return content, []

    cleaned = content
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]

    return cleaned.strip(), calls


def _apply_identity(session: Session, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """Overwrite patient identity with the authenticated session's.

    The model invents these -- a real session produced `patient_id: "12345"`.
    Identity must never come from model output, so we replace it rather than
    validate it. The rules still check ownership independently, which catches
    the case where this injection is itself broken.
    """
    if not session.actor_id:
        return tool_args
    if tool_name in ('book_appointment', 'lookup_appointments', 'update_patient_record'):
        patched = dict(tool_args)
        patched['patient_id'] = session.actor_id
        return patched
    return tool_args


def _run_gate(
    session: Session,
    request_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    user_message: str,
    agent_reply: str,
) -> Decision:
    enrichment = enrich(
        tool_name=tool_name,
        tool_args=tool_args,
        session_id=session.id,
        actor_id=session.actor_id,
    )
    return gate.evaluate(
        request_id=request_id,
        session_id=session.id,
        actor_id=session.actor_id,
        actor_kind='patient',
        identity_verified=session.identity_verified,
        tool_name=tool_name,
        tool_args=tool_args,
        user_message=user_message,
        agent_reply=agent_reply,
        enrichment=enrichment,
    )


def _record_activity(tool_name: str, session_id: str) -> None:
    kind = {
        'book_appointment': 'book',
        'cancel_appointment': 'cancel',
        'onboard_patient': 'onboard',
    }.get(tool_name)
    if kind:
        sessions.record(session_id, kind)


def _decision_dict(stage: str, tool_name: str, d: Decision) -> Dict[str, Any]:
    return {
        'stage': stage,
        'tool': tool_name or None,
        'allowed': d.allowed,
        'verdicts': d.verdicts,
        'reasons': d.reasons,
        'escalations': d.escalations,
        'flags': d.flags,
        'timed_out': d.timed_out,
        'latency_ms': round(d.latency_ms, 1),
    }


# ---------------------------------------------------------------------------
# Turn handling
# ---------------------------------------------------------------------------


def handle_message(
    *,
    message: str,
    session_id: str,
    actor_id: Optional[str] = None,
    identity_verified: bool = False,
) -> Turn:
    session = get_session(session_id, actor_id, identity_verified)
    request_id = str(uuid.uuid4())
    turn = Turn(reply='', session_id=session_id, request_id=request_id)

    session.messages.append({'role': 'user', 'content': message})

    for _ in range(settings.max_agent_steps):
        try:
            reply_msg = _chat(session.messages)
        except Exception as e:
            logger.exception('ollama call failed')
            turn.reply = f"I'm having trouble right now. Please try again shortly. ({e})"
            return turn

        tool_calls = reply_msg.get('tool_calls') or []

        if not tool_calls:
            # The model produced prose -- but it may have written a tool call
            # inside it instead of using the tool-calling API. Pull those out
            # before the text goes anywhere near the patient or the reply gate.
            raw = reply_msg.get('content', '') or ''
            candidate, text_calls = extract_text_tool_calls(raw)

            for call in text_calls:
                args = _apply_identity(session, call['name'], call['arguments'])
                turn.tool_calls.append(
                    {'name': call['name'], 'arguments': args, 'malformed': True}
                )
                if call['name'] in GATED_TOOLS:
                    # Gate it for visibility and escalation even though we will
                    # not execute it. What the model *tried* to do matters.
                    d = _run_gate(session, request_id, call['name'], args, message, '')
                    turn.decisions.append(_decision_dict('text_tool_call', call['name'], d))
                    if d.escalations:
                        turn.escalated = True
                        turn.escalation_reasons.extend(d.escalations)
                logger.warning(
                    'model wrote a tool call as text: %s (not executed)', call['name']
                )

            if text_calls:
                session.messages.append(
                    {
                        'role': 'user',
                        'content': (
                            'That tool call was written as text so it was not run. '
                            'Use the tool-calling mechanism, and do not put JSON in '
                            'your reply.'
                        ),
                    }
                )

            # Gate the REPLY before anyone sees it.
            decision = _run_gate(session, request_id, '', {}, message, candidate)
            turn.decisions.append(_decision_dict('reply', '', decision))

            if decision.escalations:
                turn.escalated = True
                turn.escalation_reasons.extend(decision.escalations)

            if decision.allowed and candidate:
                turn.reply = candidate
            else:
                turn.blocked = True
                codes = decision.denial_codes
                if 'self_harm_disclosure' in codes:
                    turn.reply = SELF_HARM_FALLBACK
                elif 'urgent_symptom' in codes:
                    turn.reply = URGENT_FALLBACK
                elif not candidate:
                    # Stripping the JSON left nothing. Don't return an empty reply.
                    turn.reply = (
                        "Sorry, let me try that again -- what would you like me to help with?"
                    )
                    turn.blocked = False
                else:
                    turn.reply = SAFE_FALLBACK
                # Keep the conversation coherent: the model should believe it
                # said the safe thing, not the blocked thing.
                candidate = turn.reply

            session.messages.append({'role': 'assistant', 'content': candidate})
            return turn

        # The model wants to call tools.
        session.messages.append(reply_msg)

        for call in tool_calls:
            fn = call.get('function', {})
            tool_name = fn.get('name', '')
            raw_args = fn.get('arguments', {})
            tool_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or '{}')

            # Identity comes from the authenticated session, never from the
            # model. Applied before enrichment so the rules see the real id.
            tool_args = _apply_identity(session, tool_name, tool_args)

            turn.tool_calls.append({'name': tool_name, 'arguments': tool_args})

            if tool_name in GATED_TOOLS:
                decision = _run_gate(session, request_id, tool_name, tool_args, message, '')
                turn.decisions.append(_decision_dict('tool_call', tool_name, decision))

                if decision.escalations:
                    turn.escalated = True
                    turn.escalation_reasons.extend(decision.escalations)

                if not decision.allowed:
                    turn.blocked = True
                    # Tell the model WHY, in the rule's own words, so it can
                    # correct course instead of retrying the same call.
                    reasons = decision.denial_reasons or ['This action is not permitted.']
                    result = {
                        'ok': False,
                        'blocked_by_policy': True,
                        'reasons': reasons,
                        'guidance': (
                            'Do not retry this call unchanged. Explain the problem to the '
                            'patient and offer a valid alternative.'
                        ),
                    }
                    session.messages.append(
                        {'role': 'tool', 'content': json.dumps(result), 'name': tool_name}
                    )
                    continue

            # Allowed (or ungated read-only tool): actually do it.
            result = execute(tool_name, tool_args)
            if result.get('ok') and tool_name in WRITE_TOOLS:
                _record_activity(tool_name, session.id)

            session.messages.append(
                {'role': 'tool', 'content': json.dumps(result), 'name': tool_name}
            )

    # Ran out of steps without the model settling on a reply.
    turn.reply = (
        "I wasn't able to complete that. Let me put you through to someone at the front desk."
    )
    turn.escalated = True
    turn.escalation_reasons.append('agent_loop_exhausted')
    return turn
