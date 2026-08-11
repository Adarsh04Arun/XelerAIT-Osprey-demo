Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# --- Unparseable time ---------------------------------------------------------
# The agent proposed something the enricher could not turn into a datetime at
# all ("next tuesday morning", "asap"). Deny explicitly rather than relying on
# the downstream rules, whose facts are all meaningless in this case.

UnparseableStartRule = Rule(
  when_all=[
    SlotParseOk == False,
  ],
  description=f'Could not interpret "{RequestedStartStr}" as an appointment time',
)

WhenRules(
  rules_any=[UnparseableStartRule],
  then=[
    DeclareVerdict(verdict='deny:unparseable_start'),
  ],
)

# --- Double booking -----------------------------------------------------------
# The single most damaging operational mistake an agent can make: two patients
# told they have the same slot. The enricher already resolved slot occupancy,
# so this is a pure comparison.

DoubleBookingRule = Rule(
  when_all=[
    SlotIsTaken == True,
  ],
  description=f'Slot {RequestedStartStr} with {ProviderIdStr} is already held by another patient',
)

WhenRules(
  rules_any=[DoubleBookingRule],
  then=[
    DeclareVerdict(verdict='deny:double_booking'),
  ],
)

# --- Booking in the past ------------------------------------------------------
# An 8B model will happily book "tomorrow" using a stale idea of today's date.

PastBookingRule = Rule(
  when_all=[
    IsPastBooking,
  ],
  description=f'Requested start {RequestedStartStr} is in the past',
)

WhenRules(
  rules_any=[PastBookingRule],
  then=[
    DeclareVerdict(verdict='deny:booking_in_past'),
  ],
)

# --- Outside clinic hours -----------------------------------------------------

OutsideClinicHoursRule = Rule(
  when_all=[
    IsPastBooking == False,
    WithinClinicHours == False,
  ],
  description=f'Requested start {RequestedStartStr} ({RequestedWeekdayStr}) is outside published clinic hours',
)

WhenRules(
  rules_any=[OutsideClinicHoursRule],
  then=[
    DeclareVerdict(verdict='deny:outside_clinic_hours'),
  ],
)

# --- Slot does not exist on the schedule --------------------------------------
# Guards against the agent inventing a time that is not on the grid at all
# (e.g. 09:07 when the clinic books on 30-minute boundaries).

UnknownSlotRule = Rule(
  when_all=[
    SlotExists == False,
    IsPastBooking == False,
    WithinClinicHours == True,
  ],
  description=f'No schedulable slot exists at {RequestedStartStr} for {ProviderIdStr}',
)

WhenRules(
  rules_any=[UnknownSlotRule],
  then=[
    DeclareVerdict(verdict='deny:slot_does_not_exist'),
  ],
)

# --- Same-day rush ------------------------------------------------------------
# Allowed, but the front desk should know it happened.

SameDayRushRule = Rule(
  when_all=[
    IsSameDayRush,
    SlotIsTaken == False,
  ],
  description=f'Booking made less than an hour before the appointment ({RequestedStartStr})',
)

WhenRules(
  rules_any=[SameDayRushRule],
  then=[
    DeclareVerdict(verdict='flag:same_day_rush'),
  ],
)
