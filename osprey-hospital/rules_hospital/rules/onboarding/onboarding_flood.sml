Import(
  rules=[
    'models/base.sml',
    'models/onboarding.sml',
  ],
)

OnboardAttempts = IncrementWindow(
  key=f'onboards-{SessionId}',
  window_seconds=10 * Minute,
  when_all=[
    IsOnboardPatient,
  ],
)

# One conversation creating patient records in bulk is either a broken agent
# loop or someone poisoning the patient index. Deny and get a human involved.

OnboardingFloodRule = Rule(
  when_all=[
    OnboardAttempts > 3,
  ],
  description=f'Session {SessionId} attempted {OnboardAttempts} patient registrations in ten minutes',
)

WhenRules(
  rules_any=[OnboardingFloodRule],
  then=[
    DeclareVerdict(verdict='deny:onboarding_rate_limit'),
    DeclareVerdict(verdict='escalate:onboarding_flood'),
  ],
)
