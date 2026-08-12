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
from .fallbacks import (
    BLOCKED_FALLBACK,
    SELF_HARM_FALLBACK,
    URGENT_FALLBACK,
    fallback_for,
)
from .osprey_gate import Decision, gate
from .provenance import fingerprint
from .tools import TOOL_SCHEMAS, WRITE_TOOLS, execute, is_gated

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

You also only handle the list above. You have no access to the practice's
business, financial, staffing or IT systems, and you do not answer general
questions unrelated to a patient's own care arrangements. If someone asks for
revenue figures, budgets, staff pay, employment matters, other patients' data,
system internals, or anything else off that list, say plainly that the request
cannot be processed and offer to help with an appointment instead. Do not guess
at a number, and do not explain what you would need in order to answer.

  Patient: "what was the hospital's revenue last quarter?"
  Good:    "I'm sorry, that request cannot be processed -- I only handle
            appointments and registration. Can I help you book something?"
  Bad:     "I don't have that to hand, but you could try the finance team on..."
  Bad:     any figure at all

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
    # Something was blocked this turn -- a tool call, a reply, or both.
    blocked: bool = False
    # The PATIENT-FACING REPLY specifically was withheld and replaced. Distinct
    # from `blocked`, because a turn where a tool call was denied and the model
    # then explained itself perfectly well is the common, healthy case: the
    # guardrail worked and the patient still got the model's own words. Marking
    # that reply as "substituted" claims the gate did something it did not, and
    # a trace that overstates the gate is as bad as one that hides it.
    reply_substituted: bool = False
    # Ordered trace of the pipeline for this turn: model -> gate -> execute ->
    # reply gate. `decisions` says what the rules concluded; `flow` says where
    # in the turn each conclusion landed and what happened next. Two proposed
    # tool calls produce two indistinguishable entries in `decisions` if they
    # name the same tool, which is precisely when you most want the order.
    flow: List[Dict[str, Any]] = field(default_factory=list)
    prompt_version: str = ''

    def step(self, kind: str, detail: str, **extra: Any) -> None:
        entry: Dict[str, Any] = {'n': len(self.flow) + 1, 'kind': kind, 'detail': detail}
        entry.update(extra)
        self.flow.append(entry)


class SessionIdentityMismatch(Exception):
    """Raised when a request reuses a session id under a different patient.

    Not a 500. The gateway turns this into a 409 -- see main.py.
    """


class Session:
    def __init__(self, session_id: str, actor_id: Optional[str], identity_verified: bool):
        self.id = session_id
        self.actor_id = actor_id
        self.identity_verified = identity_verified
        self.prompt = self._system_prompt()
        self.prompt_version = fingerprint(self.prompt)
        self.messages: List[Dict[str, Any]] = [{'role': 'system', 'content': self.prompt}]

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
        return session

    # A session carries the full message history, which contains the previous
    # patient's PHI. Handing that to a different actor because the two requests
    # happened to share a session id is a leak, and it is one no rule can catch:
    # the ruleset inspects tool arguments, and by the time it sees them the
    # wrong conversation is already in the model's context.
    #
    # This used to read `session.actor_id = actor_id or session.actor_id`, which
    # only ever widened access -- it could not represent "this is somebody else".
    if actor_id and session.actor_id and actor_id != session.actor_id:
        raise SessionIdentityMismatch(
            f'session {session_id} belongs to {session.actor_id}, not {actor_id}'
        )

    # Identity can still be established mid-conversation: an anonymous session
    # that later verifies is the normal path, and is not a mismatch.
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
    model_patient_id: Optional[str] = None,
) -> Decision:
    enrichment = enrich(
        tool_name=tool_name,
        tool_args=tool_args,
        session_id=session.id,
        actor_id=session.actor_id,
        # What the model asked for before _apply_identity overruled it. Without
        # this the ruleset sees only the corrected id, and an attempt to act on
        # someone else is invisible to the rules written to catch it.
        model_patient_id=model_patient_id,
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


def _trace_gate(turn: Turn, stage: str, tool_name: str, d: Decision) -> None:
    """Record one guardrail round trip in the turn's flow."""
    if d.timed_out:
        outcome = 'TIMED OUT -> failed closed'
    elif not d.allowed:
        outcome = 'DENY'
    elif d.verdicts:
        outcome = 'ALLOW (with observations)'
    else:
        outcome = 'ALLOW'

    turn.step(
        'gate',
        f'osprey ruled on the {stage.replace("_", " ")}: {outcome}',
        stage=stage,
        tool=tool_name or None,
        allowed=d.allowed,
        verdicts=d.verdicts,
        latency_ms=round(d.latency_ms, 1),
    )


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
    turn = Turn(
        reply='',
        session_id=session_id,
        request_id=request_id,
        prompt_version=session.prompt_version,
    )
    turn.step('user', message)

    # ---- Gate the INPUT, before the model is called at all ------------------
    #
    # Every other gate in this file inspects something the model produced, which
    # means paying for a completion first. On CPU that is 20-60 seconds, and for
    # a turn the ruleset was always going to refuse, all of it is wasted -- the
    # patient waits a minute to be told no.
    #
    # Two things change by moving a check in front of the model:
    #
    #   * speed. A refused turn ends in ~15ms. The gate was never the slow part.
    #   * certainty. A reply-side rule needs the model to draft something
    #     catchable. This one does not involve the model, so it cannot be
    #     talked round, and it holds identically on the turn where the model
    #     would have behaved.
    #
    # The second matters more. It is the difference between "the model usually
    # declines and we check its work" and "declining is not up to the model".
    #
    # Only message rules can fire here -- there is no tool call and no reply
    # yet -- so this catches self-harm, urgent symptoms, advice requests and
    # out-of-scope questions. Notably it means a suicidal disclosure now reaches
    # crisis routing in milliseconds rather than after a minute of inference.
    input_decision = _run_gate(session, request_id, '', {}, message, '')
    turn.decisions.append(_decision_dict('input', '', input_decision))
    _trace_gate(turn, 'input', '', input_decision)

    if input_decision.escalations:
        turn.escalated = True
        turn.escalation_reasons.extend(input_decision.escalations)

    if not input_decision.allowed:
        turn.blocked = True
        turn.reply_substituted = True
        turn.reply = fallback_for(input_decision.denial_codes)
        turn.step(
            'skip',
            'model never called -- the request was refused on the way in',
        )
        turn.step('reply', 'guardrail answered directly', substituted=True)
        # The turn still goes into history so the conversation stays coherent:
        # from the model's point of view it said the safe thing.
        session.messages.append({'role': 'user', 'content': message})
        session.messages.append({'role': 'assistant', 'content': turn.reply})
        return turn

    session.messages.append({'role': 'user', 'content': message})

    for step_no in range(settings.max_agent_steps):
        try:
            reply_msg = _chat(session.messages)
        except Exception as e:
            logger.exception('ollama call failed')
            turn.step('error', f'model call failed: {e}')
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
                    {'name': call['name'], 'arguments': args, 'malformed': True, 'executed': False}
                )
                turn.step(
                    'model',
                    f'wrote {call["name"]} as text, not a tool call',
                    tool=call['name'],
                    arguments=args,
                    malformed=True,
                )
                if is_gated(call['name']):
                    # Gate it for visibility and escalation even though we will
                    # not execute it. What the model *tried* to do matters.
                    d = _run_gate(session, request_id, call['name'], args, message, '')
                    turn.decisions.append(_decision_dict('text_tool_call', call['name'], d))
                    _trace_gate(turn, 'text_tool_call', call['name'], d)
                    if d.escalations:
                        turn.escalated = True
                        turn.escalation_reasons.extend(d.escalations)
                turn.step(
                    'skip',
                    f'{call["name"]} not executed -- malformed tool calls are never run',
                    tool=call['name'],
                )
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

            turn.step('model', 'drafted a reply, no tool call')

            # Gate the REPLY before anyone sees it.
            decision = _run_gate(session, request_id, '', {}, message, candidate)
            turn.decisions.append(_decision_dict('reply', '', decision))
            _trace_gate(turn, 'reply', '', decision)

            if decision.escalations:
                turn.escalated = True
                turn.escalation_reasons.extend(decision.escalations)

            if decision.allowed and candidate:
                turn.reply = candidate
                turn.step('reply', 'released to patient as drafted')
            elif decision.allowed and not candidate:
                # Nothing was blocked -- stripping a prose tool call just left
                # an empty string. Checked before the fallback because this is
                # not a refusal and must not be reported as one.
                turn.reply = (
                    "Sorry, let me try that again -- what would you like me to help with?"
                )
                turn.step('reply', 'draft was empty after stripping JSON, asked again')
            else:
                turn.blocked = True
                turn.reply_substituted = True
                turn.reply = fallback_for(decision.denial_codes)
                turn.step(
                    'reply',
                    'drafted reply withheld, guardrail substituted a safe response',
                    substituted=True,
                )
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
            original_patient = tool_args.get('patient_id')
            tool_args = _apply_identity(session, tool_name, tool_args)
            if original_patient and original_patient != tool_args.get('patient_id'):
                # Visible in the flow because it is the single most load-bearing
                # thing the gateway does, and it is otherwise invisible: the
                # rules see the corrected id and pass, so the trace would show a
                # clean booking with no sign the model named someone else.
                turn.step(
                    'identity',
                    f'patient_id from model ({original_patient}) replaced with '
                    f'session identity ({tool_args.get("patient_id")})',
                    tool=tool_name,
                )

            record: Dict[str, Any] = {
                'name': tool_name,
                'arguments': tool_args,
                'executed': False,
            }
            turn.tool_calls.append(record)
            turn.step(
                'model',
                f'proposed {tool_name}',
                tool=tool_name,
                arguments=tool_args,
                step=step_no + 1,
            )

            if is_gated(tool_name):
                decision = _run_gate(
                    session, request_id, tool_name, tool_args, message, '',
                    model_patient_id=original_patient,
                )
                turn.decisions.append(_decision_dict('tool_call', tool_name, decision))
                _trace_gate(turn, 'tool_call', tool_name, decision)

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
                    record['blocked_by'] = decision.denial_codes
                    turn.step(
                        'skip',
                        f'{tool_name} NOT executed -- nothing reached the hospital API',
                        tool=tool_name,
                    )
                    session.messages.append(
                        {'role': 'tool', 'content': json.dumps(result), 'name': tool_name}
                    )
                    continue
            else:
                turn.step(
                    'gate',
                    f'{tool_name} is an ungated read (touches no patient record)',
                    tool=tool_name,
                    ungated=True,
                )

            # Allowed (or ungated read-only tool): actually do it.
            result = execute(tool_name, tool_args)

            # Tell the model its id was overruled.
            #
            # The substitution used to be silent, and silence produced a
            # fabrication: asked to "give ajay's info", the model sent
            # patient_id="ajay", the gateway quietly rewrote it to the session
            # patient, and the model then reported the caller's OWN record back
            # as "Ajay has no upcoming appointments. Would you like to book for
            # him?" -- a confident statement about a third party, assembled
            # from someone else's data, with every guardrail reporting clean.
            #
            # Nothing leaked, because the lookup only ever touched the caller's
            # own record. The harm was the narration: correcting the action
            # while leaving the model believing its version happened means the
            # model describes an event that did not occur. A control that
            # rewrites a request has to tell whoever it overruled, or it just
            # moves the error from the data to the sentence.
            if original_patient and original_patient != tool_args.get('patient_id'):
                result = dict(result, identity_note=(
                    f'This result is for {tool_args.get("patient_id")}, the authenticated '
                    f'caller. It is NOT for "{original_patient}" -- you cannot look up '
                    f'anyone else, and no data about them was returned. Do not describe '
                    f'this result as belonging to anyone but the caller, and tell them '
                    f'you can only access their own record.'
                ))

            record['executed'] = True
            record['ok'] = bool(result.get('ok'))
            if result.get('ok') and tool_name in WRITE_TOOLS:
                _record_activity(tool_name, session.id)

            turn.step(
                'execute',
                f'{tool_name} ran against the hospital API'
                + ('' if result.get('ok') else f' and failed: {result.get("error", "unknown")}'),
                tool=tool_name,
                ok=bool(result.get('ok')),
            )

            session.messages.append(
                {'role': 'tool', 'content': json.dumps(result), 'name': tool_name}
            )

    # Ran out of steps without the model settling on a reply.
    turn.step(
        'error',
        f'model still calling tools after {settings.max_agent_steps} steps -- loop capped',
    )
    turn.reply = (
        "I wasn't able to complete that. Let me put you through to someone at the front desk."
    )
    turn.escalated = True
    turn.escalation_reasons.append('agent_loop_exhausted')
    return turn
