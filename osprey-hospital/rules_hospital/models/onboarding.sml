Import(
  rules=[
    'models/base.sml',
  ],
)

# --- Fields the agent collected ----------------------------------------------

OnboardFullName: str = JsonData(
  path='$.tool_args.full_name',
  coerce_type=True,
  required=False,
)

OnboardDateOfBirth: str = JsonData(
  path='$.tool_args.date_of_birth',
  coerce_type=True,
  required=False,
)

OnboardPhone: str = JsonData(
  path='$.tool_args.phone',
  coerce_type=True,
  required=False,
)

OnboardInsuranceId: str = JsonData(
  path='$.tool_args.insurance_id',
  coerce_type=True,
  required=False,
)

OnboardConsentGiven: bool = JsonData(
  path='$.tool_args.consent_given',
  coerce_type=True,
  required=False,
)

# Free-text the agent tried to stash on the record. This is where clinical
# content leaks in -- an operational agent has no business writing a chief
# complaint or an assessment into a patient record.
OnboardNotes: str = JsonData(
  path='$.tool_args.notes',
  coerce_type=True,
  required=False,
)

UpdateField: str = JsonData(
  path='$.tool_args.field',
  coerce_type=True,
  required=False,
)

UpdateValue: str = JsonData(
  path='$.tool_args.value',
  coerce_type=True,
  required=False,
)

# --- Enrichment ---------------------------------------------------------------

PatientExists: bool = JsonData(
  path='$.enrichment.patient.exists',
  coerce_type=True,
  required=False,
)

# Did the enricher find an existing patient with the same name + DOB?
DuplicatePatientFound: bool = JsonData(
  path='$.enrichment.patient.duplicate_found',
  coerce_type=True,
  required=False,
)

DuplicatePatientId: str = JsonData(
  path='$.enrichment.patient.duplicate_id',
  coerce_type=True,
  required=False,
)

# NOTE: TargetIsVerifiedActor now lives in models/base.sml -- appointment rules
# need it too. Defining it in both places is a duplicate-definition error.

SessionOnboardsLastTenMin: int = JsonData(
  path='$.enrichment.session.onboards_last_10m',
  coerce_type=True,
  required=False,
)

# Fields a non-clinical agent is permitted to write.
UpdateFieldIsAllowed: bool = InList(
  value=UpdateField,
  values=['phone', 'email', 'address', 'insurance_id', 'preferred_language', 'emergency_contact'],
)

# --- Derived ------------------------------------------------------------------

HasFullName: bool = StringNotEmpty(s=OnboardFullName)
HasDateOfBirth: bool = StringNotEmpty(s=OnboardDateOfBirth)
HasPhone: bool = StringNotEmpty(s=OnboardPhone)

OnboardingFieldsComplete: bool = HasFullName and HasDateOfBirth and HasPhone

# Notes the agent wants to persist, scanned for clinical language.
OnboardNotesClinicalTerm: Optional[str] = PhraseListMatch(
  list='clinical_terms',
  text=OnboardNotes,
)

# See the note in models/appointment.sml: a description that interpolates a
# value which is None at runtime makes the whole rule evaluate to null, so the
# guardrail silently fails open. Coerce everything used in a description.
OnboardNotesClinicalTermStr: str = ForceString(s=OnboardNotesClinicalTerm)
OnboardFullNameStr: str = ForceString(s=OnboardFullName, default='(no name given)')
OnboardDateOfBirthStr: str = ForceString(s=OnboardDateOfBirth, default='(no DOB given)')
DuplicatePatientIdStr: str = ForceString(s=DuplicatePatientId, default='(unknown)')
UpdateFieldStr: str = ForceString(s=UpdateField, default='(no field given)')
OnboardActorIdStr: str = ForceString(s=ActorId, default='(anonymous)')
