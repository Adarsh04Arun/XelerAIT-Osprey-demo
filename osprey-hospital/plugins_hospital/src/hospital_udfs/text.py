"""Text-matching UDFs for the hospital ruleset.

Deliberately small. The atproto plugin has a much richer text stdlib
(tokenizing, censorship-normalisation, domain extraction); none of that is
needed to decide whether a scheduling bot just gave medical advice.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional

import yaml
from osprey.engine.executor.execution_context import ExecutionContext
from osprey.engine.stdlib.udfs.categories import UdfCategories
from osprey.engine.udf.arguments import ArgumentsBase
from osprey.engine.udf.base import UDFBase
from osprey.worker.lib.osprey_shared.logging import get_logger

logger = get_logger('hospital_text')


def _lists_dir() -> str:
    """Locate the ruleset's `lists/` directory.

    OSPREY_RULES_PATH points at the ruleset root (e.g. /osprey/rules_hospital),
    which is where the worker was told to load SML from.
    """
    rules_path = os.environ.get('OSPREY_RULES_PATH', '/osprey/rules_hospital')
    return os.path.join(rules_path, 'lists')


@lru_cache(maxsize=32)
def load_phrase_list(name: str) -> tuple[str, ...]:
    """Load and normalise a phrase list. Cached -- these files never change at runtime."""
    path = os.path.join(_lists_dir(), f'{name}.yaml')
    try:
        with open(path, encoding='utf-8') as fh:
            raw = yaml.safe_load(fh) or []
    except FileNotFoundError:
        logger.error(f'phrase list not found: {path}')
        return ()
    except Exception as e:
        logger.error(f'failed to load phrase list {path}: {e}')
        return ()

    if not isinstance(raw, list):
        logger.error(f'phrase list {path} must be a YAML list, got {type(raw).__name__}')
        return ()

    return tuple(str(item).strip().lower() for item in raw if str(item).strip())


class PhraseListMatchArguments(ArgumentsBase):
    list: str
    """Name of a YAML file in the ruleset's `lists/` directory, without the extension."""

    text: Optional[str]
    """Text to scan. None/empty is not a match."""


class PhraseListMatch(UDFBase[PhraseListMatchArguments, Optional[str]]):
    """Return the first phrase from `list` found in `text`, or None.

    Returning the matched phrase rather than a bool is what lets a rule say
    *why* it fired -- the description f-strings interpolate it, so a blocked
    turn is explainable without digging through logs.
    """

    category = UdfCategories.STRING

    def execute(
        self, execution_context: ExecutionContext, arguments: PhraseListMatchArguments
    ) -> Optional[str]:
        text = arguments.text
        if not text:
            return None

        haystack = text.lower()
        for phrase in load_phrase_list(arguments.list):
            if phrase in haystack:
                return phrase
        return None


class StringNotEmptyArguments(ArgumentsBase):
    s: Optional[str]


class StringNotEmpty(UDFBase[StringNotEmptyArguments, bool]):
    """True when `s` has non-whitespace content. Guards the `required=False`
    JsonData fields, which arrive as None when the agent omitted them."""

    category = UdfCategories.STRING

    def execute(self, execution_context: ExecutionContext, arguments: StringNotEmptyArguments) -> bool:
        return bool(arguments.s and arguments.s.strip())


class ForceStringArguments(ArgumentsBase):
    s: Optional[str]
    default: str = ''


class ForceString(UDFBase[ForceStringArguments, str]):
    """Coerce `Optional[str]` to `str`.

    SML refuses to interpolate an optional into an f-string ("unsupported type
    for f-string substitution"). Since PhraseListMatch returns the matched
    phrase or None, rule descriptions that name the match need this to narrow
    the type. rules_atproto carries a UDF of the same name for the same reason.
    """

    category = UdfCategories.STRING

    def execute(self, execution_context: ExecutionContext, arguments: ForceStringArguments) -> str:
        return arguments.s if arguments.s is not None else arguments.default


class InListArguments(ArgumentsBase):
    value: Optional[str]
    values: List[str]


class InList(UDFBase[InListArguments, bool]):
    """Case-insensitive allowlist membership, for small literal lists written
    inline in SML (the field allowlist in models/onboarding.sml)."""

    category = UdfCategories.STRING

    def execute(self, execution_context: ExecutionContext, arguments: InListArguments) -> bool:
        if not arguments.value:
            return False
        needle = arguments.value.strip().lower()
        return any(needle == str(v).strip().lower() for v in arguments.values)
