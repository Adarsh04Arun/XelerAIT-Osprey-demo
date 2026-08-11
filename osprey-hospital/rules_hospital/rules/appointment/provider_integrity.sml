Import(
  rules=[
    'models/appointment.sml',
    'models/base.sml',
  ],
)

# --- Unknown provider ---------------------------------------------------------
# Models hallucinate plausible-sounding doctors. This catches it before the
# patient is told to show up for someone who does not work here.

UnknownProviderRule = Rule(
  when_all=[
    ProviderExists == False,
  ],
  description=f'Provider {ProviderIdStr} does not exist',
)

WhenRules(
  rules_any=[UnknownProviderRule],
  then=[
    DeclareVerdict(verdict='deny:unknown_provider'),
  ],
)

# --- Provider not available ---------------------------------------------------
# On leave, blocked out, or not taking appointments that day.

ProviderUnavailableRule = Rule(
  when_all=[
    ProviderExists == True,
    ProviderIsAvailable == False,
  ],
  description=f'Provider {ProviderIdStr} is not available on {RequestedStartStr}',
)

WhenRules(
  rules_any=[ProviderUnavailableRule],
  then=[
    DeclareVerdict(verdict='deny:provider_unavailable'),
  ],
)

# --- Daily capacity -----------------------------------------------------------
# Overbooking a clinician is how a schedule quietly becomes fiction.

ProviderOverCapacityRule = Rule(
  when_all=[
    ProviderExists == True,
    ProviderAtCapacity,
  ],
  description=f'Provider {ProviderIdStr} is at their daily cap of {ProviderDailyCap} appointments',
)

WhenRules(
  rules_any=[ProviderOverCapacityRule],
  then=[
    DeclareVerdict(verdict='deny:provider_at_capacity'),
  ],
)

# --- Department mismatch ------------------------------------------------------
# Routing a patient to the wrong specialty is an operational error the agent
# can make without saying anything clinical. Flagged rather than denied: the
# front desk may have a reason.

DepartmentMismatchRule = Rule(
  when_all=[
    ProviderExists == True,
    StringNotEmpty(s=Department),
    StringNotEmpty(s=ProviderDepartment),
    Department != ProviderDepartment,
  ],
  description=f'Requested department {DepartmentStr} does not match {ProviderIdStr} ({ProviderDepartmentStr})',
)

WhenRules(
  rules_any=[DepartmentMismatchRule],
  then=[
    DeclareVerdict(verdict='flag:department_mismatch'),
  ],
)
