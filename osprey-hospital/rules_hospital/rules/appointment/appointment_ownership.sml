Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# --- Identity gate ------------------------------------------------------------
# Nothing touches a patient record until the gateway has verified who it is
# talking to. This is the broadest PHI guardrail in the ruleset.

UnverifiedRecordAccessRule = Rule(
  when_all=[
    IdentityVerified == False,
  ],
  description='Agent attempted to access a patient record before identity was verified',
)

WhenRules(
  rules_any=[UnverifiedRecordAccessRule],
  then=[
    DeclareVerdict(verdict='deny:identity_not_verified'),
  ],
)

# --- Cross-patient access -----------------------------------------------------
# The verified caller is trying to read or move an appointment belonging to
# somebody else. This is the one that leaks another person's PHI, so it also
# escalates rather than just failing quietly.

CrossPatientAccessRule = Rule(
  when_all=[
    IdentityVerified == True,
    AppointmentExists == True,
    AppointmentBelongsToActor == False,
  ],
  description=f'Actor {ActorIdStr} attempted to act on appointment {AppointmentIdStr} belonging to another patient',
)

WhenRules(
  rules_any=[CrossPatientAccessRule],
  then=[
    DeclareVerdict(verdict='deny:cross_patient_access'),
    DeclareVerdict(verdict='escalate:phi_boundary'),
  ],
)

# --- Unknown appointment ------------------------------------------------------

UnknownAppointmentRule = Rule(
  when_all=[
    IdentityVerified == True,
    StringNotEmpty(s=AppointmentId),
    AppointmentExists == False,
  ],
  description=f'Appointment {AppointmentIdStr} does not exist',
)

WhenRules(
  rules_any=[UnknownAppointmentRule],
  then=[
    DeclareVerdict(verdict='deny:unknown_appointment'),
  ],
)
