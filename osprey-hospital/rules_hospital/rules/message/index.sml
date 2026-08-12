# Imports and requires only -- no rule logic here.
#
# These run on EVERY turn, tool call or not. The agent can violate scope purely
# by talking, so gating only on tool calls would miss the main failure mode.

Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

Require(rule='rules/message/clinical_scope.sml')
Require(rule='rules/message/urgent_triage.sml')

# The outer scope boundary -- everything the agent has no mandate over at all.
# Unconditional for the same reason clinical_scope is: the violation can be
# pure conversation, and gating only on tool calls would miss all of it.
Require(rule='rules/message/out_of_scope.sml')

# Instruction-override and prompt-extraction attempts. Unconditional, and it
# matters that this runs at the `input` stage: an injection only works if the
# payload reaches the model, so a rule that waits to inspect the model's output
# has already lost the round.
Require(rule='rules/message/prompt_injection.sml')

# Unconditional and never behind a require_if guard. This is the one rule that
# must run on every single turn regardless of what the agent is doing.
Require(rule='rules/message/self_harm.sml')
