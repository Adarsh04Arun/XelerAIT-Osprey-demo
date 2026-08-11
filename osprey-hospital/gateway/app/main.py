"""FastAPI gateway -- the server half of the client/server MVP.

Routes:
    POST /chat              one conversational turn, with the full guardrail trace
    GET  /audit             what the effector recorded (PHI-redacted)
    GET  /escalations       turns that need a human
    GET  /hospital/state    current schedule, for eyeballing what actually changed
    POST /admin/reset       reset hospital + sessions + counters (test fixture)
    GET  /healthz
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import agent as agent_mod
from .config import settings
from .effector import apply_effects, audit_log, escalations
from .enricher import enrich, sessions
from .hospital import store
from .osprey_gate import gate

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
)
logger = logging.getLogger('hospital.gateway')


@asynccontextmanager
async def lifespan(app: FastAPI):
    gate.start()
    logger.info('gateway ready: model=%s ollama=%s', settings.ollama_model, settings.ollama_host)
    yield
    gate.stop()


app = FastAPI(
    title='Hospital Operations Agent (Osprey-gated)',
    description='An operational hospital agent whose every action is gated by an Osprey ruleset.',
    version='0.1.0',
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default='sess-default')
    # Who the gateway believes it is talking to. In a real deployment this comes
    # from an authenticated session, never from the request body -- the rules
    # treat identity_verified as trusted input.
    actor_id: Optional[str] = None
    identity_verified: bool = False


class EvaluateRequest(BaseModel):
    """A proposed action, stated directly rather than produced by the model."""

    tool_name: str = ''
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default='sess-eval')
    actor_id: Optional[str] = None
    identity_verified: bool = False
    user_message: str = ''
    agent_reply: str = ''


class EvaluateResponse(BaseModel):
    allowed: bool
    verdicts: List[str]
    denial_codes: List[str]
    reasons: List[Dict[str, Any]]
    escalations: List[str]
    flags: List[str]
    timed_out: bool
    latency_ms: float


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    request_id: str
    blocked: bool
    escalated: bool
    escalation_reasons: List[str]
    tool_calls: List[Dict[str, Any]]
    decisions: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post('/chat', response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    turn = agent_mod.handle_message(
        message=req.message,
        session_id=req.session_id,
        actor_id=req.actor_id,
        identity_verified=req.identity_verified,
    )
    apply_effects(turn, req.message)

    return ChatResponse(
        reply=turn.reply,
        session_id=turn.session_id,
        request_id=turn.request_id,
        blocked=turn.blocked,
        escalated=turn.escalated,
        escalation_reasons=list(dict.fromkeys(turn.escalation_reasons)),
        tool_calls=turn.tool_calls,
        decisions=turn.decisions,
    )


@app.post('/evaluate', response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    """Run a proposed action through the ruleset without involving the model.

    This is how you test guardrails. Driving rules through /chat means asserting
    on llama3.1's choices, which vary run to run -- a test that fails because
    the model phrased something differently tells you nothing about your rules.
    Here the input is stated exactly, so a failure means a rule changed.

    Same enrichment and same ruleset as /chat -- only the source of the proposed
    action differs.

    Note this does NOT run the effector: no audit entry, no escalation. Probe
    traffic would otherwise drown the real audit trail and page a human for
    every test run. The consequence is that an escalation raised here is visible
    only in the response body, so don't use /evaluate to exercise the
    escalation pipeline -- use /chat for that.
    """
    enrichment = enrich(
        tool_name=req.tool_name,
        tool_args=req.tool_args,
        session_id=req.session_id,
        actor_id=req.actor_id,
    )
    decision = gate.evaluate(
        request_id=f'eval-{req.session_id}',
        session_id=req.session_id,
        actor_id=req.actor_id,
        actor_kind='patient',
        identity_verified=req.identity_verified,
        tool_name=req.tool_name,
        tool_args=req.tool_args,
        user_message=req.user_message,
        agent_reply=req.agent_reply,
        enrichment=enrichment,
    )
    return EvaluateResponse(
        allowed=decision.allowed,
        verdicts=decision.verdicts,
        denial_codes=decision.denial_codes,
        reasons=decision.reasons,
        escalations=decision.escalations,
        flags=decision.flags,
        timed_out=decision.timed_out,
        latency_ms=round(decision.latency_ms, 1),
    )


@app.get('/audit')
def get_audit(limit: int = 100) -> Dict[str, Any]:
    return {'entries': audit_log.entries(limit)}


@app.get('/escalations')
def get_escalations(limit: int = 100) -> Dict[str, Any]:
    return {'escalations': escalations.items(limit)}


@app.get('/hospital/state')
def hospital_state() -> Dict[str, Any]:
    return {
        'providers': store.provider_summary(),
        'patients': [
            {'id': p.id, 'full_name': p.full_name, 'date_of_birth': p.date_of_birth}
            for p in store.patients.values()
        ],
        'appointments': [asdict(a) for a in store.appointments.values()],
    }


@app.post('/admin/reset')
def reset() -> Dict[str, str]:
    """Reset every piece of gateway state. Used between test scenarios.

    Note this does NOT reset the rules engine's own IncrementWindow counters --
    those live in the worker process. Tests that need clean rate-limit state
    should use a fresh session_id, which is what the window keys are built on.
    """
    store.seed()
    sessions.reset()
    agent_mod.reset_sessions()
    audit_log.clear()
    escalations.clear()
    return {'status': 'reset'}


class SeedAppointmentRequest(BaseModel):
    patient_id: str
    provider_id: str
    start: str
    department: str = ''


@app.post('/admin/seed-appointment')
def seed_appointment(req: SeedAppointmentRequest) -> Dict[str, Any]:
    """Write an appointment straight into the store, bypassing the gate.

    A TEST FIXTURE, not a code path the agent can reach. It exists so a test
    can put the schedule into a specific state -- e.g. filling a provider to
    their daily cap -- without driving the model through N conversations first.

    Deliberately separate from the agent's tools: nothing the model can call
    reaches this, so bypassing the guardrails here cannot leak into real use.
    """
    provider = store.providers.get(req.provider_id)
    if provider is None:
        return {'ok': False, 'error': f'unknown provider {req.provider_id}'}
    try:
        apt = store.book(
            patient_id=req.patient_id,
            provider_id=req.provider_id,
            start=req.start,
            department=req.department or provider.department,
        )
        return {'ok': True, 'appointment': asdict(apt)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@app.get('/healthz')
def healthz() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'model': settings.ollama_model,
        'fail_open': settings.fail_open,
        'verdict_timeout_seconds': settings.verdict_timeout_seconds,
    }
