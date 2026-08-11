Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# Identity and ownership checks for CREATING an appointment.
#
# These were missing. `appointment_ownership.sml` covers reschedule, cancel and
# lookup -- all of which reference an existing appointment id -- but booking
# creates a new record and so was never required, leaving two holes:
#
#   * an unverified caller could create bookings
#   * a verified caller could book under any patient id, including one the model
#     invented (a real session produced patient_id "12345")
#
# The gateway also now overwrites patient_id from the authenticated session
# before it ever reaches here, so in normal operation these rules should never
# fire. They are the backstop for a gateway bug -- if this ever denies something,
# the injection upstream has failed.

# --- Identity gate ------------------------------------------------------------

UnverifiedBookingRule = Rule(
  when_all=[
    IdentityVerified == False,
  ],
  description='Agent attempted to create an appointment before identity was verified',
)

WhenRules(
  rules_any=[UnverifiedBookingRule],
  then=[
    DeclareVerdict(verdict='deny:identity_not_verified'),
  ],
)

# --- Booking on someone else's behalf -----------------------------------------
# Either the model hallucinated a patient id, or somebody is trying to occupy
# another patient's slot. Both need a human to look.

BookingForAnotherPatientRule = Rule(
  when_all=[
    IdentityVerified == True,
    TargetIsVerifiedActor == False,
  ],
  description=f'Actor {ActorIdStr} attempted to book an appointment for a different patient ({PatientIdStr})',
)

WhenRules(
  rules_any=[BookingForAnotherPatientRule],
  then=[
    DeclareVerdict(verdict='deny:cross_patient_booking'),
    DeclareVerdict(verdict='escalate:phi_boundary'),
  ],
)
