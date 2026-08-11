"""Publishes rule verdicts back to the gateway so it can gate on them.

This is the piece that turns Osprey -- an async, fire-and-forget stream engine --
into something a request can block on.

osprey-atproto never needs this: its effector consumes results whenever they
arrive and calls Ozone after the fact. A hospital agent cannot work that way,
because "the booking was wrong" is only useful *before* the patient is told
their appointment is confirmed.

So: the gateway stamps each proposed action with a unique action_id and waits.
This sink writes the verdicts to a reply topic keyed by that id. The gateway
correlates and unblocks. Standard request/reply over Kafka -- Osprey itself is
unmodified and still just runs rules.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from kafka import KafkaProducer
from osprey.engine.executor.execution_context import ExecutionResult
from osprey.worker.lib.osprey_shared.logging import get_logger
from osprey.worker.sinks.sink.output_sink import BaseOutputSink

logger = get_logger('hospital-verdict-sink')


class VerdictKafkaSink(BaseOutputSink):
    """Emit `{action_id, verdicts, rules, features}` to the verdict topic."""

    def __init__(self, bootstrap_servers: List[str], topic: str, client_id: str | None = None):
        if not bootstrap_servers:
            raise ValueError('VerdictKafkaSink requires at least one bootstrap server')
        if not topic:
            raise ValueError('VerdictKafkaSink requires an output topic')

        self._topic = topic
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id or 'hospital-verdict-sink',
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: str(k).encode('utf-8'),
            # The gateway is holding a request open waiting for this. Favour
            # latency over throughput: send immediately, don't batch.
            linger_ms=0,
            acks=1,
            retries=3,
            max_block_ms=5000,
        )
        logger.info(f'VerdictKafkaSink publishing to {topic} via {bootstrap_servers}')

    def will_do_work(self, result: ExecutionResult) -> bool:
        # Always. The gateway blocks on a reply for every action it sends, so a
        # clean action still needs an explicit "no verdicts" message -- silence
        # here would mean a timeout there.
        return True

    def push(self, result: ExecutionResult) -> None:
        try:
            self._producer.send(
                self._topic,
                key=result.action.action_id,
                value=self._build_payload(result),
            )
            # Don't wait for the broker ack; the send is queued and linger_ms=0
            # means it goes out on the next network cycle. Blocking here would
            # stall the rules worker on every event.
        except Exception as e:
            # Never let a sink failure take down rules processing. The gateway
            # will time out and fail closed, which is the safe direction.
            logger.exception(f'failed to publish verdict for action {result.action.action_id}: {e}')

    @staticmethod
    def _render(rule: Any) -> str:
        """Substitute a rule's resolved values into its description template.

        Osprey stores `RuleT.description` as the raw f-string template and puts
        the resolved variables in `RuleT.features` -- deliberate, so the UI can
        group every firing of a rule under one identity. We want the opposite
        here: the denial reason is shown to a patient and written to the audit
        log, so it needs the actual slot time and provider in it.
        """
        template = rule.description or ''
        features = rule.features or {}
        if not features:
            return template
        try:
            return template.format(**features)
        except (KeyError, IndexError, ValueError):
            # A description referencing something not in features shouldn't cost
            # us the reason entirely -- fall back to the template.
            return template

    @classmethod
    def _build_payload(cls, result: ExecutionResult) -> Dict[str, Any]:
        verdicts = [v.verdict for v in result.verdicts]

        # Which rules produced each verdict -- this is what makes a denial
        # explainable to the patient and auditable afterwards.
        reasons: List[Dict[str, Any]] = []
        for effect in result.verdicts:
            for rule in effect.rules:
                reasons.append(
                    {
                        'verdict': effect.verdict,
                        'rule': rule.name,
                        'description': cls._render(rule),
                    }
                )

        data = result.action.data or {}

        return {
            'action_id': str(result.action.action_id),
            'request_id': data.get('request_id'),
            'session_id': data.get('session_id'),
            'tool_name': data.get('tool_name'),
            'verdicts': verdicts,
            'reasons': reasons,
            'error_count': len(result.error_infos),
        }

    def stop(self) -> None:
        try:
            self._producer.flush(timeout=5)
            self._producer.close(timeout=5)
        except Exception as e:
            logger.warning(f'error closing verdict producer: {e}')
