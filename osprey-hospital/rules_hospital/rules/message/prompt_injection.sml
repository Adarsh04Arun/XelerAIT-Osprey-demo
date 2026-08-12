Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

# Attempts to override the agent's instructions or read its prompt.
#
# Runs at the `input` stage, so the turn ends before the model is called. That
# ordering is the point: an injection only works if the payload reaches the
# model, and a rule that inspects the model's *output* has already lost the
# round it was meant to win. Everything the gateway caught on the observed
# attempt -- a hallucinated patient id, an unprompted lookup, spontaneous
# ibuprofen advice -- happened because the message got through to be reasoned
# about at all.
#
# WHAT THIS IS NOT: an injection defence. It matches literal phrasings, so a
# rewording, a translation, base64, or an instruction split across two turns
# goes through untouched. No list closes that, and the file that feeds this rule
# exists precisely because the previous list missed "ignore all the rules" while
# containing "ignore all previous".
#
# The defences that actually hold do not require recognising the attack:
#
#   * identity is injected server-side, so a model talked into naming another
#     patient still acts on the authenticated one
#   * the ruleset gates the ACTION, so a persuaded model still cannot book
#     across a PHI boundary or write to a chart
#   * the reply is checked whatever the model was convinced to draft
#
# On the observed attempt all three held while this rule did not exist. Read
# that as the layering working, not as this rule being decorative -- it is the
# cheapest layer and the only one that costs the attacker a turn.
#
# Escalated, unlike an out-of-scope question. Someone asking about revenue is
# confused; someone typing "ignore all the rules" is establishing whether the
# controls are real, and that is worth a human knowing even though the attempt
# failed. It sits in the `high` band with PHI boundary events -- below self-harm,
# above routine noise.

PromptInjectionRule = Rule(
  when_all=[
    UserMessageIsInjectionAttempt,
  ],
  description=f'Message attempts to override the agent instructions or read its prompt (matched: {UserMessageInjectionTermStr})',
)

WhenRules(
  rules_any=[PromptInjectionRule],
  then=[
    DeclareVerdict(verdict='deny:prompt_injection'),
    DeclareVerdict(verdict='escalate:injection_attempt'),
  ],
)

# --- The model acted on it ----------------------------------------------------
#
# A write proposed on the same turn as an override attempt. The input gate ends
# the turn before this can normally fire, so it exists for the case where the
# input gate is bypassed -- /evaluate, a future caller that skips the input
# stage, or a payload that arrives mid-conversation rather than as the opening
# message. Cheap, and the one combination where an injection has actually
# reached something that changes state.

InjectionWithWriteRule = Rule(
  when_all=[
    UserMessageIsInjectionAttempt,
    IsWriteTool,
  ],
  description='Agent proposed a write on a turn containing an instruction-override attempt',
)

WhenRules(
  rules_any=[InjectionWithWriteRule],
  then=[
    DeclareVerdict(verdict='deny:prompt_injection'),
    DeclareVerdict(verdict='escalate:injection_mishandled'),
  ],
)
