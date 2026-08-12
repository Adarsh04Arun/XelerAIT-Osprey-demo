"""What produced a decision -- model, ruleset, prompt.

An audit entry that records *what* was decided but not *what decided it* cannot
answer the only question anyone asks afterwards: "why did it do that?" By the
time someone looks, the model may have been re-pulled and the ruleset edited,
and the entry is unfalsifiable.

Three identifiers, stamped on every audit row:

    model_version    the pinned Ollama tag actually in use
    ruleset_version  content hash of rules_hospital/ (SML + lists)
    prompt_version   content hash of the rendered system prompt

None of these needs a registry or a version service. They are content hashes,
so two runs that agree produced identical inputs, and two runs that differ tell
you which input moved. That is the cheap 80% of reproducibility; the expensive
20% (immutable storage, retention windows, replay-by-id) is deliberately out of
scope for this MVP and is listed as such in the README.
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Optional

from .config import settings

logger = logging.getLogger('hospital.provenance')

# The ruleset is mounted read-only into the gateway purely so it can be hashed.
# The worker is what actually executes it; this container never reads a rule.
RULESET_PATH = os.environ.get('HOSPITAL_RULESET_PATH', '/srv/rules_hospital')

# Files that make up the policy. A change to any of them changes the verdicts,
# so all of them belong in the hash -- the lists especially, since a phrase
# added to a YAML file is a policy change that touches no .sml at all.
_POLICY_SUFFIXES = ('.sml', '.yaml', '.yml')


def fingerprint(text: str) -> str:
    """Short content hash. Twelve hex chars is plenty to spot a change."""
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:12]


@lru_cache(maxsize=1)
def ruleset_version(path: Optional[str] = None) -> str:
    """Content hash of every policy file, in a stable order.

    Cached: the ruleset is compiled once at worker startup and bind-mounted
    read-only, so it cannot change under a running gateway. Restarting the
    worker to pick up a rule change also restarts this.
    """
    root = path or RULESET_PATH
    digest = hashlib.sha256()
    counted = 0

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                if not name.endswith(_POLICY_SUFFIXES):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, '/')
                # Hash the path as well as the content, so moving a rule between
                # files registers as a change even if the bytes are identical.
                digest.update(rel.encode('utf-8'))
                with open(full, 'rb') as fh:
                    digest.update(fh.read())
                counted += 1
    except OSError as e:
        logger.warning('cannot hash ruleset at %s (%s) -- provenance degraded', root, e)
        return 'unavailable'

    if counted == 0:
        logger.warning('no policy files found at %s -- provenance degraded', root)
        return 'unavailable'

    return digest.hexdigest()[:12]


def stamp() -> dict:
    """The provenance block attached to every audit entry."""
    return {
        'model_version': settings.ollama_model,
        'ruleset_version': ruleset_version(),
    }
