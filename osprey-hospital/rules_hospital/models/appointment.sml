Import(
  rules=[
    'models/base.sml',
  ],
)

# --- What the agent asked for -------------------------------------------------

ProviderId: Entity[str] = EntityJson(
  type='Provider',
  path='$.tool_args.provider_id',
  coerce_type=True,
  required=False,
)

PatientId: Entity[str] = EntityJson(
  type='Patient',
  path='$.tool_args.patient_id',
  coerce_type=True,
  required=False,
)

# ISO-8601 local time the agent wants to book, e.g. "2026-08-14T09:30"
RequestedStart: str = JsonData(
  path='$.tool_args.start',
  coerce_type=True,
  required=False,
)

AppointmentId: str = JsonData(
  path='$.tool_args.appointment_id',
  coerce_type=True,
  required=False,
)

Department: str = JsonData(
  path='$.tool_args.department',
  coerce_type=True,
  required=False,
)

# --- What the enricher looked up BEFORE the rules ran -------------------------
#
# gateway/app/enricher.py fills all of this in by calling the hospital API,
# the scheduling service and the session store. By the time we get here every
# fact below is already resolved -- rules stay pure.

# Slot state
SlotExists: bool = JsonData(
  path='$.enrichment.slot.exists',
  coerce_type=True,
  required=False,
)

SlotIsTaken: bool = JsonData(
  path='$.enrichment.slot.is_taken',
  coerce_type=True,
  required=False,
)

SlotTakenBy: str = JsonData(
  path='$.enrichment.slot.taken_by',
  coerce_type=True,
  required=False,
)

# Minutes from "now" to the requested start. Negative == in the past.
MinutesUntilStart: float = JsonData(
  path='$.enrichment.slot.minutes_until_start',
  coerce_type=True,
  required=False,
)

# Is the requested start inside the clinic's published opening hours?
WithinClinicHours: bool = JsonData(
  path='$.enrichment.slot.within_clinic_hours',
  coerce_type=True,
  required=False,
)

RequestedWeekday: str = JsonData(
  path='$.enrichment.slot.weekday',
  coerce_type=True,
  required=False,
)

# Provider state
ProviderExists: bool = JsonData(
  path='$.enrichment.provider.exists',
  coerce_type=True,
  required=False,
)

ProviderIsAvailable: bool = JsonData(
  path='$.enrichment.provider.is_available',
  coerce_type=True,
  required=False,
)

ProviderDailyCap: int = JsonData(
  path='$.enrichment.provider.daily_cap',
  coerce_type=True,
  required=False,
)

ProviderBookedToday: int = JsonData(
  path='$.enrichment.provider.booked_on_requested_day',
  coerce_type=True,
  required=False,
)

ProviderDepartment: str = JsonData(
  path='$.enrichment.provider.department',
  coerce_type=True,
  required=False,
)

# Existing appointment (for reschedule / cancel)
AppointmentExists: bool = JsonData(
  path='$.enrichment.appointment.exists',
  coerce_type=True,
  required=False,
)

AppointmentBelongsToActor: bool = JsonData(
  path='$.enrichment.appointment.belongs_to_actor',
  coerce_type=True,
  required=False,
)

# Session counters, maintained by the enricher's session store.
SessionBookingsLastFiveMin: int = JsonData(
  path='$.enrichment.session.bookings_last_5m',
  coerce_type=True,
  required=False,
)

SessionCancelsLastTenMin: int = JsonData(
  path='$.enrichment.session.cancels_last_10m',
  coerce_type=True,
  required=False,
)

SessionRebookChurn: int = JsonData(
  path='$.enrichment.session.book_cancel_cycles',
  coerce_type=True,
  required=False,
)

# Did the enricher manage to parse the time the agent proposed at all?
SlotParseOk: bool = JsonData(
  path='$.enrichment.slot.parse_ok',
  coerce_type=True,
  required=False,
)

# --- Derived ------------------------------------------------------------------

IsPastBooking: bool = SlotParseOk and MinutesUntilStart < 0

# Booking something less than an hour out is allowed but is worth flagging.
# Guarded on SlotParseOk: an unparsed time reports 0 minutes-until-start, which
# would otherwise look exactly like an imminent appointment.
IsSameDayRush: bool = SlotParseOk and MinutesUntilStart >= 0 and MinutesUntilStart < 60

ProviderAtCapacity: bool = ProviderBookedToday >= ProviderDailyCap

# --- String coercions for rule descriptions -----------------------------------
#
# Anything interpolated into a Rule(description=f'...') must be non-optional at
# RUNTIME, not just declared non-optional. If a description references a value
# that turns out to be None, the whole rule evaluates to null -- it does not
# fire, and no verdict is emitted.
#
# That failure mode is silent and it fails OPEN, which is the worst possible
# direction for a guardrail: an unparseable appointment time was sailing through
# precisely because the rule meant to catch it named a weekday that did not
# exist. Coerce here, and only use these in descriptions.

RequestedStartStr: str = ForceString(s=RequestedStart, default='(unparseable)')
RequestedWeekdayStr: str = ForceString(s=RequestedWeekday, default='(unknown day)')
ProviderIdStr: str = ForceString(s=ProviderId, default='(none given)')
PatientIdStr: str = ForceString(s=PatientId, default='(none given)')
AppointmentIdStr: str = ForceString(s=AppointmentId, default='(none given)')
DepartmentStr: str = ForceString(s=Department, default='(unspecified)')
ProviderDepartmentStr: str = ForceString(s=ProviderDepartment, default='(unknown)')
ActorIdStr: str = ForceString(s=ActorId, default='(anonymous)')
