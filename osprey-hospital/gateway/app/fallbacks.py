"""What the patient is told when a turn is blocked.

Separated from agent.py for two reasons.

The practical one: agent.py reaches Kafka through osprey_gate, so importing it
requires the whole stack. This text is the most safety-critical string content
in the project and it should be testable with nothing running -- see
tests/test_effector.py, which imports only this.

The substantive one: these are policy, not plumbing. Which words a person in
crisis reads is a decision of the same kind as which rule fires, and it does not
belong buried three hundred lines into a tool-calling loop.
"""

from __future__ import annotations

from typing import List

# THE blocked response. One message, whatever was blocked.
#
# There used to be a different wording per category -- a medical decline, a
# scope refusal, a generic apology -- and the variation was a liability rather
# than a courtesy. A refusal that tells you which rule caught it is a probe
# oracle: ask, read which flavour comes back, adjust, repeat. Answering
# identically every time means a blocked turn reveals only that it was blocked.
#
# It is also more honest. The gateway does not know why the model was about to
# say something wrong, only that it was, and a tailored apology implies an
# understanding of the request that the system does not have.
BLOCKED_FALLBACK = (
    'Sorry, that request cannot be processed. I can help with appointments, '
    'registration, and questions about opening hours, location and parking. '
    'Would you like me to help with any of those?'
)

URGENT_FALLBACK = (
    "That sounds like it needs attention right away, and it's not something I "
    'can help with as a scheduling assistant. Please call emergency services '
    "now, or our urgent care line. I'm flagging this to a member of staff."
)

# Deliberately does not offer to book anything, and does not ask questions.
# The failing behaviour this replaces was the model saying "I can't help with
# that" and then offering an appointment slot.
SELF_HARM_FALLBACK = (
    "I'm really glad you told me, and I want to make sure you get proper "
    'support right now rather than an appointment slot.\n\n'
    'Please contact one of these straight away:\n'
    '  - Emergency services, if you are in immediate danger\n'
    '  - Samaritans: 116 123 (free, 24 hours a day)\n'
    '  - Text SHOUT to 85258\n\n'
    "I'm alerting a member of our staff now so someone can reach out to you. "
    "You don't have to deal with this on your own."
)


# Every blocked turn gets BLOCKED_FALLBACK. These two are the only exceptions,
# and they are exceptions on safety grounds rather than presentation.
#
# A uniform refusal is right for a scope violation, a PHI boundary, a malformed
# call, a rate limit -- for all of those "cannot be processed" is accurate and
# gives an adversary nothing. It is catastrophic for exactly two categories,
# because in both the patient has disclosed something where the *content* of
# the reply is the safety control:
#
#   self-harm  -- someone who says "I want to die" needs Samaritans and
#                 emergency services. A flat refusal is a door closing on a
#                 person in crisis, and it is the last thing they hear from us.
#   urgent     -- someone reporting chest pain needs "call 999 now". Telling
#                 them their request cannot be processed reads as a system
#                 fault, and the plausible response to a system fault is to
#                 wait and try again.
#
# In both cases the block is not the safety measure; the replacement text is.
# Collapsing them into the generic message would remove the control while
# leaving every appearance of it in place -- the rule still fires, the audit
# still records a save, and nobody is helped.
#
# Order matters because one turn can trip several rules. A suicidal disclosure
# that also mentions chest pain also reads as clinical; the patient sees one
# reply, and it has to be the crisis one.
_CRISIS_FALLBACKS = (
    ('self_harm_disclosure', SELF_HARM_FALLBACK),
    ('urgent_symptom', URGENT_FALLBACK),
)


def fallback_for(denial_codes: List[str]) -> str:
    """The replacement reply for a blocked turn.

    Always returns something: a crisis message when one of those two codes is
    present, BLOCKED_FALLBACK for everything else -- including codes that do
    not exist yet. A rule added next year inherits the uniform refusal by
    default rather than by somebody remembering to wire it up.
    """
    for code, text in _CRISIS_FALLBACKS:
        if code in denial_codes:
            return text
    return BLOCKED_FALLBACK
