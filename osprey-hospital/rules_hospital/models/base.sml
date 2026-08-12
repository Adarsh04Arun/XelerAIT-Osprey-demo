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
# Call identity
# ---------------------------------------------------------------------------
#
# Hash of (tool_name, tool_args), resolved by the enricher because rules cannot
# hash anything themselves. Used by rules/duplicate_call.sml to tell "the agent
# is working through a problem" apart from "the agent is stuck repeating one
# rejected call".
CallFingerprint: str = JsonData(
  path='$.enrichment.call.fingerprint',
  coerce_type=True,
  required=False,
)

CallFingerprintStr: str = ForceString(s=CallFingerprint, default='(none)')

# Did the model actually decide anything, or fill every field with a
# placeholder? See enrich_call_shape() in gateway/app/enricher.py.
CallHasUsableArgs: bool = JsonData(
  path='$.enrichment.call.has_usable_args',
  coerce_type=True,
  required=False,
)

CallNullLikeArgCount: int = JsonData(
  path='$.enrichment.call.null_like_count',
  coerce_type=True,
  required=False,
)

# ToolName is declared non-optional, but "declared" and "true at runtime" are
# different things, and a description that interpolates a None makes the whole
# rule evaluate to null and fail OPEN. See the fourth bug in the README. Coerce
# anything that reaches an f-string, without exception.
ToolNameStr: str = ForceString(s=ToolName, default='(no tool)')

# Lives here rather than in models/appointment.sml because ActorId is declared
# here and rules outside the appointment tree need it -- patient_scope.sml is
# attached to no operation and so imports no per-type model. Defining it in both
# places is a duplicate-definition error, so this is the one copy.
ActorIdStr: str = ForceString(s=ActorId, default='(anonymous)')

# ---------------------------------------------------------------------------
# What the model asked for, before the gateway overruled it
# ---------------------------------------------------------------------------
#
# _apply_identity replaces patient_id with the authenticated session's before
# enrichment runs, so every ownership rule sees the CORRECTED id and passes.
# That is the right order -- the action must be made safe first -- but it means
# the model's intent to act on someone else leaves no trace in the ruleset.
#
# Observed: "give ajay's info" produced patient_id="ajay", which the gateway
# rewrote to the caller. Not one rule fired, because by the time the rules ran
# there was nothing wrong to see.
IdentitySubstituted: bool = JsonData(
  path='$.enrichment.patient.identity_substituted',
  coerce_type=True,
  required=False,
)

ModelSuppliedPatientId: str = JsonData(
  path='$.enrichment.patient.model_supplied_id',
  coerce_type=True,
  required=False,
)

ModelSuppliedPatientIdStr: str = ForceString(s=ModelSuppliedPatientId, default='(none)')

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
