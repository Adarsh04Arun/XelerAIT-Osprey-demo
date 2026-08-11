# Imports and requires only -- no rule logic here.

Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

Require(
  rule='rules/onboarding/required_fields.sml',
  require_if=IsOnboardPatient,
)

Require(
  rule='rules/onboarding/duplicate_patient.sml',
  require_if=IsOnboardPatient,
)

Require(
  rule='rules/onboarding/record_write_scope.sml',
  require_if=IsUpdatePatientRecord,
)

Require(
  rule='rules/onboarding/onboarding_flood.sml',
  require_if=IsOnboardPatient,
)
