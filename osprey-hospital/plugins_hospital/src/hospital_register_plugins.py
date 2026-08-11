"""Osprey plugin registration for the hospital operations ruleset.

Loaded via the `osprey_plugin` setuptools entry point declared in
plugins_hospital/pyproject.toml.

This plugin registers UDFs and one extra output sink. It deliberately does NOT
register an input stream -- the gateway produces plain JSON straight to
`osprey.actions_input` and Osprey's built-in Kafka input stream handles it, so
there is nothing to customise on the way in.

It also does not register a labels service; example_plugins already provides
the Postgres-backed one, and `register_labels_service_or_provider` is a
firstresult hook (two implementations would fight).

Naming matters here: every plugin's modules land on the same sys.path, and
example_plugins already owns the top-level names `register_plugins` and `udfs`.
Reusing either would shadow it and silently break the stock plugin (its
BanUser/TextContains UDFs would vanish, taking example_rules with them). Hence
the `hospital_` prefixes -- do not "tidy" them away.
"""

from collections.abc import Sequence
from typing import Any, Type

from osprey.engine.udf.base import UDFBase
from osprey.worker.adaptor.plugin_manager import hookimpl_osprey
from osprey.worker.lib.config import Config
from osprey.worker.lib.osprey_shared.logging import get_logger
from osprey.worker.sinks.sink.output_sink import BaseOutputSink

from hospital_sinks.verdict_kafka_sink import VerdictKafkaSink
from hospital_udfs.text import ForceString, InList, PhraseListMatch, StringNotEmpty
from hospital_udfs.window import GetWindowCount, IncrementWindow

logger = get_logger('hospital-plugin')


@hookimpl_osprey
def register_udfs() -> Sequence[Type[UDFBase[Any, Any]]]:
    """UDFs used by rules_hospital.

    Note what is absent: there is no UDF here that calls an external service.
    That is the same contract osprey-atproto holds to -- every fact a rule needs
    was already fetched by the enricher before the event was produced. Rules
    stay pure, fast, and testable in isolation.
    """
    return [
        PhraseListMatch,
        StringNotEmpty,
        ForceString,
        InList,
        IncrementWindow,
        GetWindowCount,
    ]


@hookimpl_osprey
def register_output_sinks(config: Config) -> Sequence[BaseOutputSink]:
    """Add the verdict reply sink alongside whatever else is configured."""
    bootstrap_servers = config.get_str_list('OSPREY_KAFKA_BOOTSTRAP_SERVERS', ['localhost:9092'])
    topic = config.get_str('HOSPITAL_VERDICT_TOPIC', 'hospital.verdicts')
    client_id = config.get_optional_str('HOSPITAL_VERDICT_CLIENT_ID')

    logger.info(f'registering VerdictKafkaSink -> {topic}')
    return [
        VerdictKafkaSink(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            client_id=client_id,
        )
    ]
