Import(
  rules=[
    'models/base.sml',
  ],
)

# One ownership check covering every tool that names a patient.
#
# The specific rules -- BookingForAnotherPatientRule, CrossPatientWriteRule,
# CrossPatientAccessRule -- still exist and still give precise verdicts. This is
# the backstop underneath them, and it exists because the specific rules are
# each attached to a named operation, and the set of named operations has now
# been wrong twice:
#
#   1. book_appointment had no identity gate, because ownership lived in
#      appointment_ownership.sml which was required only for reschedule, cancel
#      and lookup. Written up in the README. Fixed by adding booking to the list.
#
#   2. lookup_appointments had no patient-ownership check at all. The ownership
#      rules that did run keyed on an appointment_id, which a lookup does not
#      carry, so pat-1001 could request pat-1002's appointments and NOTHING
#      fired. Found by probing the ruleset directly rather than through /chat,
#      because the gateway's identity injection made it unreachable in normal
#      use -- a hole that was real and invisible simultaneously.
#
# The fix for (1) was to add an entry to the list. That is why (2) happened. So
# this rule is deliberately not attached to any operation: it fires whenever the
# event names a patient who is not the verified caller, whatever the tool is,
# including tools that do not exist yet.
#
# Note what it does NOT depend on: the user's message, the agent's reply, or
# anything the model said. A caller who has talked the model into believing it
# is an administrator still arrives here, and the comparison is unchanged --
# this id, that id, equal or not. That property is what makes the action layer
# hold under jailbreaks while the phrase lists do not.

CrossPatientRecordRule = Rule(
  when_all=[
    IdentityVerified == True,
    IsPatientRecordTool,
    TargetIsVerifiedActor == False,
  ],
  description=f'Actor {ActorIdStr} named a different patient on a {ToolNameStr} call',
)

WhenRules(
  rules_any=[CrossPatientRecordRule],
  then=[
    DeclareVerdict(verdict='deny:cross_patient_access'),
    DeclareVerdict(verdict='escalate:phi_boundary'),
  ],
)

# --- The model tried to act for someone else ----------------------------------
#
# Flagged rather than denied, and the distinction is the whole point of the rule.
#
# By the time this evaluates, the gateway has already replaced the patient id
# with the authenticated caller's, so the ACTION is safe and denying it would
# only stop the caller reading their own record. There is nothing left to
# prevent. What is left is a fact worth recording: the model, on this turn,
# tried to operate on a patient who is not the caller.
#
# Without this the event is invisible. "give ajay's info" produced
# patient_id="ajay", the gateway rewrote it, every gate reported clean, and the
# only evidence was a line in the CLI trace that nothing persists. An audit
# trail that cannot distinguish "asked for their own record" from "asked for
# someone else's and was silently corrected" cannot answer the question anyone
# would actually ask afterwards.
#
# Not escalated. Most instances are the model hallucinating an id -- the README
# records patient_id "12345" -- and paging a human for that would bury the real
# signals. It is a flag: visible in the audit, countable, ignorable.

IdentitySubstitutedRule = Rule(
  when_all=[
    IdentitySubstituted == True,
  ],
  description=f'Model named patient {ModelSuppliedPatientIdStr} on a {ToolNameStr} call; gateway substituted the authenticated caller',
)

WhenRules(
  rules_any=[IdentitySubstitutedRule],
  then=[
    DeclareVerdict(verdict='flag:identity_substituted'),
  ],
)

# Identity, same shape. UnverifiedRecordAccessRule and UnverifiedRecordWriteRule
# cover this for the operations they are attached to; this one is attached to
# none of them.

UnverifiedRecordToolRule = Rule(
  when_all=[
    IdentityVerified == False,
    IsPatientRecordTool,
  ],
  description=f'Unverified caller attempted {ToolNameStr} against a patient record',
)

WhenRules(
  rules_any=[UnverifiedRecordToolRule],
  then=[
    DeclareVerdict(verdict='deny:identity_not_verified'),
  ],
)
