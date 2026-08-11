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

# Unconditional and never behind a require_if guard. This is the one rule that
# must run on every single turn regardless of what the agent is doing.
Require(rule='rules/message/self_harm.sml')
