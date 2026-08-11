Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

# --- Incomplete record --------------------------------------------------------
# A patient record created without name/DOB/phone is a record nobody can match
# later. The agent must go back and ask rather than writing a stub.

IncompleteOnboardingRule = Rule(
  when_all=[
    OnboardingFieldsComplete == False,
  ],
  description='Onboarding attempted without full name, date of birth and phone',
)

WhenRules(
  rules_any=[IncompleteOnboardingRule],
  then=[
    DeclareVerdict(verdict='deny:onboarding_incomplete'),
  ],
)

# --- Consent ------------------------------------------------------------------
# No record is created before the patient has consented. Separate from the
# field check so the denial reason tells the agent what to actually do.

MissingConsentRule = Rule(
  when_all=[
    OnboardingFieldsComplete == True,
    OnboardConsentGiven == False,
  ],
  description='Onboarding attempted without recorded patient consent',
)

WhenRules(
  rules_any=[MissingConsentRule],
  then=[
    DeclareVerdict(verdict='deny:consent_missing'),
  ],
)

# --- Clinical content in the notes field --------------------------------------
# The scope boundary as it appears in *written* form. An operational agent may
# record "prefers morning appointments"; it may not record a chief complaint.

ClinicalNotesRule = Rule(
  when_all=[
    OnboardNotesClinicalTerm != None,
  ],
  description=f'Agent attempted to write clinical content to a patient record (matched: {OnboardNotesClinicalTermStr})',
)

WhenRules(
  rules_any=[ClinicalNotesRule],
  then=[
    DeclareVerdict(verdict='deny:clinical_content_in_record'),
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)
