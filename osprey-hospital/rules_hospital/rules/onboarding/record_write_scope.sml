Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

# --- Identity gate ------------------------------------------------------------

UnverifiedRecordWriteRule = Rule(
  when_all=[
    IdentityVerified == False,
  ],
  description='Agent attempted to modify a patient record before identity was verified',
)

WhenRules(
  rules_any=[UnverifiedRecordWriteRule],
  then=[
    DeclareVerdict(verdict='deny:identity_not_verified'),
  ],
)

# --- Writing to somebody else's chart -----------------------------------------

CrossPatientWriteRule = Rule(
  when_all=[
    IdentityVerified == True,
    TargetIsVerifiedActor == False,
  ],
  description=f'Actor {OnboardActorIdStr} attempted to modify a record belonging to another patient',
)

WhenRules(
  rules_any=[CrossPatientWriteRule],
  then=[
    DeclareVerdict(verdict='deny:cross_patient_write'),
    DeclareVerdict(verdict='escalate:phi_boundary'),
  ],
)

# --- Field allowlist ----------------------------------------------------------
# An operational agent updates contact and insurance details. It does not touch
# allergies, medications, diagnoses, or anything else on the clinical side of
# the chart -- those require a clinician, not a scheduling bot.

DisallowedFieldWriteRule = Rule(
  when_all=[
    IdentityVerified == True,
    UpdateFieldIsAllowed == False,
  ],
  description=f'Agent attempted to write to non-operational field "{UpdateFieldStr}"',
)

WhenRules(
  rules_any=[DisallowedFieldWriteRule],
  then=[
    DeclareVerdict(verdict='deny:field_out_of_scope'),
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)
