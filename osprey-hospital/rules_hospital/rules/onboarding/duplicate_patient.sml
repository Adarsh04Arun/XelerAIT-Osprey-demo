Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

# Duplicate medical records are a genuine patient-safety problem: care gets
# split across two charts. The enricher matched on name + date of birth.

DuplicatePatientRule = Rule(
  when_all=[
    DuplicatePatientFound == True,
  ],
  description=f'A patient record already exists for {OnboardFullNameStr} ({OnboardDateOfBirthStr}): {DuplicatePatientIdStr}',
)

WhenRules(
  rules_any=[DuplicatePatientRule],
  then=[
    DeclareVerdict(verdict='deny:duplicate_patient'),
  ],
)
