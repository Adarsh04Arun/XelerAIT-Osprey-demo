Import(
  rules=[
    'models/base.sml',
  ],
)

# A circuit breaker on the same call, made over and over.
#
# The existing rate limits in rules/appointment/booking_churn.sml count by tool
# KIND: five bookings in five minutes. That catches volume but not repetition,
# and the two are different failures. Five different bookings is a busy patient.
# The same rejected booking five times is a model that has not understood the
# denial and is retrying verbatim -- the exact behaviour the README records
# under "tool discipline", where llama3.1 proposed
# book_appointment(start="find_open_slots") and then proposed it again.
#
# Until now the only thing discouraging that was a sentence in the denial
# payload: "Do not retry this call unchanged." That is a prompt instruction
# handed to a model that has already demonstrated it does not follow prompt
# instructions reliably. This is the same rule enforced rather than requested.
#
# It counts denied attempts as well as allowed ones, which is the whole point --
# IncrementWindow increments on attempt, so a call denied four times is at four,
# not zero.

RepeatedCallAttempts = IncrementWindow(
  key=f'call-{SessionId}-{CallFingerprint}',
  window_seconds=2 * Minute,
  when_all=[
    StringNotEmpty(s=CallFingerprint),
  ],
)

# Three, not two. A single retry is legitimate: the model corrects an argument,
# the fingerprint changes, and this never fires. A second identical attempt can
# be a genuine race. A third means nothing is being learned from the response,
# and every further attempt is load on the hospital API for no possible gain.

RepeatedIdenticalCallRule = Rule(
  when_all=[
    RepeatedCallAttempts > 3,
  ],
  description=f'Agent repeated the identical {ToolNameStr} call {RepeatedCallAttempts} times in two minutes without changing anything',
)

WhenRules(
  rules_any=[RepeatedIdenticalCallRule],
  then=[
    DeclareVerdict(verdict='deny:repeated_identical_call'),
    DeclareVerdict(verdict='escalate:agent_stuck'),
  ],
)
