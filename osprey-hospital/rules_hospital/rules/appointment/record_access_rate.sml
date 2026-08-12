Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# Reads were not counted at all until now, and that was a real gap rather than
# an omission of tidiness.
#
# Every windowed counter in this ruleset watched writes: bookings, cancels,
# registrations. The reasoning was that reads change nothing, so there is
# nothing to rate limit. That reasoning is wrong for anything touching PHI.
# Individually compliant reads can sum to a disclosure that no single one of
# them represents -- the same shape as structuring, where each transfer clears
# the threshold and the sequence does not. Twenty lookups in five minutes on a
# verified session is not twenty people checking their appointment; it is
# something walking the record space.
#
# The ownership rules in appointment_ownership.sml already stop a caller reading
# ANOTHER patient's appointments. This is the volume dimension they cannot see,
# because each call they inspect is legitimate on its own.

LookupAttempts = IncrementWindow(
  key=f'lookups-{SessionId}',
  window_seconds=5 * Minute,
  when_all=[
    IsLookupAppointments,
  ],
)

# --- Unusual read volume ------------------------------------------------------
# Flagged, not denied. A patient re-checking their booking three times because
# the first reply was unclear is ordinary, and denying a read of your own record
# is a bad trade for a signal this soft. The front desk gets to see it.

HighReadVolumeRule = Rule(
  when_all=[
    LookupAttempts > 8,
  ],
  description=f'Session {SessionId} made {LookupAttempts} record lookups in five minutes',
)

WhenRules(
  rules_any=[HighReadVolumeRule],
  then=[
    DeclareVerdict(verdict='flag:high_read_volume'),
  ],
)

# --- Sustained enumeration ----------------------------------------------------
# Past a certain point the benign reading stops being available. Denied, and
# escalated under the PHI band rather than a rate-limit band -- what is at stake
# is patient data, not scheduling capacity.

RecordEnumerationRule = Rule(
  when_all=[
    LookupAttempts > 20,
  ],
  description=f'Session {SessionId} made {LookupAttempts} record lookups in five minutes -- possible enumeration',
)

WhenRules(
  rules_any=[RecordEnumerationRule],
  then=[
    DeclareVerdict(verdict='deny:read_rate_limit'),
    DeclareVerdict(verdict='escalate:phi_boundary'),
  ],
)
