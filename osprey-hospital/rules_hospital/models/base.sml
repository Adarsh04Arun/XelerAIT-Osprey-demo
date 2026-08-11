# Values common to every event the gateway sends us.
#
# Event envelope produced by gateway/app/osprey_gate.py:
#
#   { "request_id", "session_id", "actor_id", "actor_kind",
#     "identity_verified", "tool_name", "tool_args": {...},
#     "user_message", "agent_reply", "enrichment": {...} }
#
# Everything under `enrichment` was fetched by the enricher BEFORE the rules ran.
# Rules never call an API themselves -- same contract as osprey-atproto.

RequestId: Entity[str] = EntityJson(
  type='RequestId',
  path='$.request_id',
  coerce_type=True,
)

SessionId: Entity[str] = EntityJson(
  type='Session',
  path='$.session_id',
  coerce_type=True,
)

# The person the agent is acting on behalf of.
ActorId: Entity[str] = EntityJson(
  type='Actor',
  path='$.actor_id',
  coerce_type=True,
)

ActorKind: str = JsonData(
  path='$.actor_kind',
  coerce_type=True,
  required=False,
)

# Did the gateway verify who it is talking to before this call?
IdentityVerified: bool = JsonData(
  path='$.identity_verified',
  coerce_type=True,
  required=False,
)

# The tool the agent wants to invoke. This is a PROPOSAL -- nothing has run yet.
ToolName: str = JsonData(
  path='$.tool_name',
  coerce_type=True,
)

UserMessage: str = JsonData(
  path='$.user_message',
  coerce_type=True,
  required=False,
)

AgentReply: str = JsonData(
  path='$.agent_reply',
  coerce_type=True,
  required=False,
)

ActionName = GetActionName()
ActionId = GetActionId()

# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------

IsBookAppointment: bool = ToolName == 'book_appointment'
IsRescheduleAppointment: bool = ToolName == 'reschedule_appointment'
IsCancelAppointment: bool = ToolName == 'cancel_appointment'
IsLookupAppointments: bool = ToolName == 'lookup_appointments'
IsOnboardPatient: bool = ToolName == 'onboard_patient'
IsUpdatePatientRecord: bool = ToolName == 'update_patient_record'

# Anything that mutates hospital state. These are the calls that must be gated.
IsWriteTool: bool = (
  IsBookAppointment
  or IsRescheduleAppointment
  or IsCancelAppointment
  or IsOnboardPatient
  or IsUpdatePatientRecord
)

# Anything that reads a patient record.
IsPatientRecordTool: bool = (
  IsLookupAppointments
  or IsUpdatePatientRecord
  or IsBookAppointment
  or IsRescheduleAppointment
  or IsCancelAppointment
)

IsAppointmentTool: bool = (
  IsBookAppointment
  or IsRescheduleAppointment
  or IsCancelAppointment
  or IsLookupAppointments
)

IsOnboardingTool: bool = IsOnboardPatient or IsUpdatePatientRecord

# ---------------------------------------------------------------------------
# Record targeting
# ---------------------------------------------------------------------------
#
# Is the patient record this action touches the same person the gateway
# verified? Lives in base.sml rather than a per-type model because BOTH
# appointment and onboarding rules need it -- booking is a write against a
# patient id just as much as a record update is.
#
# It was originally only in models/onboarding.sml, which is exactly how
# book_appointment ended up with no ownership check at all: an unverified
# caller could book, and a verified caller could book under any patient id the
# model invented.
TargetIsVerifiedActor: bool = JsonData(
  path='$.enrichment.patient.target_is_verified_actor',
  coerce_type=True,
  required=False,
)

# ---------------------------------------------------------------------------
# Time constants (seconds), same convention as rules_atproto/models/base.sml
# ---------------------------------------------------------------------------

Second: float = 1
Minute: float = Second * 60
FiveMinute: float = Minute * 5
TenMinute: float = Minute * 10
ThirtyMinute: float = Minute * 30
Hour: float = Minute * 60
Day: float = Hour * 24
Week: float = Day * 7
