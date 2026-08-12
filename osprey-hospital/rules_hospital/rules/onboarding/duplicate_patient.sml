Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

# Duplicate medical records are a genuine patient-safety problem: care gets
# split across two charts. The enricher matched on name + date of birth.
#
# NOTE THE WORDING, and do not "improve" it by adding the details back.
#
# This description used to read:
#
#     A patient record already exists for {name} ({dob}): {patient_id}
#
# which is an excellent audit line and a registration oracle. Rule descriptions
# are fed back to the model as denial reasons so it can explain itself, and the
# model relays them to the caller -- so anyone could submit a guessed name and
# date of birth and have the system confirm the match AND hand back the internal
# patient id. No tool call succeeded, no record was read, and PHI came out
# anyway, through the refusal.
#
# The general problem is that a description has two audiences with opposite
# requirements: the audit log wants every identifying detail, the patient must
# receive none of them. One string cannot serve both, and where they conflict
# the string is written for the patient. The audit still has what it needs --
# effector.py records the tool arguments (redacted) and the rules that fired,
# so the duplicate is reconstructable from the entry without it being quotable
# at the person who probed for it.

DuplicatePatientRule = Rule(
  when_all=[
    DuplicatePatientFound == True,
  ],
  description='A patient record matching those details already exists. Do not state the name, date of birth or record id -- ask the caller to confirm their details with reception.',
)

WhenRules(
  rules_any=[DuplicatePatientRule],
  then=[
    DeclareVerdict(verdict='deny:duplicate_patient'),
  ],
)
