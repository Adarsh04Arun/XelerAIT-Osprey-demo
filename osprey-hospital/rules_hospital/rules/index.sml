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

# Shape checks on the proposed call itself, before anything asks what it means.
# Required for any event carrying a tool call, whatever the tool: a model
# proposing an empty call, or the same call over and over, is not a property of
# appointments.
Require(
  rule='rules/malformed_call.sml',
  require_if=StringNotEmpty(s=ToolName),
)

Require(
  rule='rules/duplicate_call.sml',
  require_if=StringNotEmpty(s=ToolName),
)

# Patient ownership, attached to no particular operation on purpose. The
# per-tool ownership rules have missed an operation twice; this one cannot,
# because it does not know what the operations are. See patient_scope.sml.
Require(
  rule='rules/patient_scope.sml',
  require_if=IsPatientRecordTool,
)

Require(
  rule='rules/appointment/index.sml',
  require_if=IsAppointmentTool,
)

Require(
  rule='rules/onboarding/index.sml',
  require_if=IsOnboardingTool,
)
