# Conditionally require rulesets based on what the agent is trying to do.
# Same pattern as rules_atproto/rules/index.sml -- keep this file to imports
# and requires only.

Import(
  rules=[
    'models/base.sml',
  ],
)

# Scope containment runs on every turn, including turns with no tool call at
# all, because the violation can live purely in what the agent says.
Require(
  rule='rules/message/index.sml',
)

Require(
  rule='rules/appointment/index.sml',
  require_if=IsAppointmentTool,
)

Require(
  rule='rules/onboarding/index.sml',
  require_if=IsOnboardingTool,
)
