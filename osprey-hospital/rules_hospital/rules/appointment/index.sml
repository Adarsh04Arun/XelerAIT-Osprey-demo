# Imports and requires only -- no rule logic here.

Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# Slot-level integrity. Only meaningful when a concrete start time was proposed.
Require(
  rule='rules/appointment/slot_integrity.sml',
  require_if=IsBookAppointment or IsRescheduleAppointment,
)

# Provider-level integrity.
Require(
  rule='rules/appointment/provider_integrity.sml',
  require_if=IsBookAppointment or IsRescheduleAppointment,
)

# Ownership of an EXISTING appointment.
Require(
  rule='rules/appointment/appointment_ownership.sml',
  require_if=IsRescheduleAppointment or IsCancelAppointment or IsLookupAppointments,
)

# Identity and ownership for CREATING one. Separate file because booking has no
# appointment id to check against -- it validates the patient id instead. Leaving
# booking out of the ownership guard above is what let an unverified caller book.
Require(
  rule='rules/appointment/booking_identity.sml',
  require_if=IsBookAppointment,
)

# Windowed abuse counters for writes.
Require(
  rule='rules/appointment/booking_churn.sml',
  require_if=IsBookAppointment or IsCancelAppointment,
)

# And for reads. Reads were uncounted until a review pointed out that the one
# thing no write counter can see is a caller quietly reading a lot.
Require(
  rule='rules/appointment/record_access_rate.sml',
  require_if=IsLookupAppointments,
)
