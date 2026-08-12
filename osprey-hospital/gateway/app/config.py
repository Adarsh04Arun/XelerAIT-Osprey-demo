"""Gateway configuration. Everything is env-overridable so the same code runs
under docker compose and on the host during development."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _csv(name: str, default: str) -> List[str]:
    return [p.strip() for p in os.environ.get(name, default).split(',') if p.strip()]


@dataclass
class Settings:
    # --- Kafka / Osprey ------------------------------------------------------
    kafka_bootstrap_servers: List[str] = field(
        default_factory=lambda: _csv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
    )
    actions_topic: str = os.environ.get('OSPREY_ACTIONS_TOPIC', 'osprey.actions_input')
    verdict_topic: str = os.environ.get('HOSPITAL_VERDICT_TOPIC', 'hospital.verdicts')

    # How long to wait for Osprey to rule on a proposed action. On a local
    # stack the round trip is tens of milliseconds; this is a safety net, not
    # a target.
    verdict_timeout_seconds: float = float(os.environ.get('HOSPITAL_VERDICT_TIMEOUT', '8.0'))

    # What to do when Osprey does not answer in time. Failing CLOSED means a
    # broken guardrail pipeline stops the agent acting rather than letting it
    # run unsupervised -- the right default for anything touching a patient.
    fail_open: bool = os.environ.get('HOSPITAL_FAIL_OPEN', 'false').lower() == 'true'

    # --- Ollama --------------------------------------------------------------
    ollama_host: str = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')

    # PINNED, deliberately. `llama3.1:latest` is a floating tag: Ollama can
    # resolve it to a different build after a `pull`, and the agent's behaviour
    # changes with no code change on our side. Every claim in the README about
    # what the model actually did was measured against 8b. Bump this on purpose,
    # then run tests/test_agent_behaviour.py -- that suite exists to make a
    # version bump safe rather than hopeful.
    ollama_model: str = os.environ.get('OLLAMA_MODEL', 'llama3.1:8b')
    ollama_timeout_seconds: float = float(os.environ.get('OLLAMA_TIMEOUT', '120'))

    # Low temperature: this agent should be boring and repeatable. Raise it if
    # you want to stress the guardrails with more erratic model behaviour.
    ollama_temperature: float = float(os.environ.get('OLLAMA_TEMPERATURE', '0.2'))

    # Cap the tool-calling loop so a confused model can't spin forever.
    max_agent_steps: int = int(os.environ.get('HOSPITAL_MAX_AGENT_STEPS', '5'))

    # --- Clinic --------------------------------------------------------------
    clinic_open_hour: int = int(os.environ.get('CLINIC_OPEN_HOUR', '9'))
    clinic_close_hour: int = int(os.environ.get('CLINIC_CLOSE_HOUR', '17'))
    slot_minutes: int = int(os.environ.get('CLINIC_SLOT_MINUTES', '30'))

    # 0 = Monday. Weekend closed.
    open_weekdays: tuple = (0, 1, 2, 3, 4)

    log_level: str = os.environ.get('LOG_LEVEL', 'INFO')


settings = Settings()
