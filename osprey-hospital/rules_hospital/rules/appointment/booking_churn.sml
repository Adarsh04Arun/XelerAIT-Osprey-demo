Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# Windowed counters, in the spirit of rules_atproto's IncrementWindow. Ours are
# incremented in the rules engine itself so the count reflects attempts that
# were denied too -- an agent looping on a rejected booking is exactly the
# signal we want.
#
# NOTE: IncrementWindow here is backed by an in-process store in the worker
#       (see plugins_hospital/src/udfs/hospital/window.py). Single-worker only.
#       Swap for memcached/redis before running more than one rules worker.

BookingAttempts = IncrementWindow(
  key=f'bookings-{SessionId}',
  window_seconds=5 * Minute,
  when_all=[
    IsBookAppointment,
  ],
)

CancelAttempts = IncrementWindow(
  key=f'cancels-{SessionId}',
  window_seconds=10 * Minute,
  when_all=[
    IsCancelAppointment,
  ],
)

# --- Booking flood ------------------------------------------------------------
# One conversation should not produce six bookings in five minutes. Either the
# agent is stuck in a retry loop or somebody is squatting slots.

BookingFloodRule = Rule(
  when_all=[
    IsBookAppointment,
    BookingAttempts > 5,
  ],
  description=f'Session {SessionId} attempted {BookingAttempts} bookings in five minutes',
)

WhenRules(
  rules_any=[BookingFloodRule],
  then=[
    DeclareVerdict(verdict='deny:booking_rate_limit'),
    DeclareVerdict(verdict='escalate:booking_flood'),
  ],
)

# --- Cancel/rebook churn ------------------------------------------------------
# Thrashing the schedule. Not denied -- a patient genuinely rearranging their
# week looks like this -- but the front desk should see it.

CancelChurnRule = Rule(
  when_all=[
    CancelAttempts > 3,
  ],
  description=f'Session {SessionId} cancelled {CancelAttempts} appointments in ten minutes',
)

WhenRules(
  rules_any=[CancelChurnRule],
  then=[
    DeclareVerdict(verdict='flag:cancel_churn'),
  ],
)
