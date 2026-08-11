Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

# Someone describing chest pain or difficulty breathing to a scheduling bot is
# the highest-stakes moment in this entire system. The agent must not offer to
# "find the next available slot" -- the turn is taken away from it and handed
# to a human immediately.
#
# This fires regardless of whether the agent deferred: deferring politely is
# not enough when the answer needed is "call emergency services now".

UrgentSymptomRule = Rule(
  when_all=[
    UserMessageIsUrgent,
  ],
  description=f'Patient described a potentially urgent symptom (matched: {UserMessageUrgentTermStr})',
)

WhenRules(
  rules_any=[UrgentSymptomRule],
  then=[
    DeclareVerdict(verdict='deny:urgent_symptom'),
    DeclareVerdict(verdict='escalate:urgent_triage'),
  ],
)

# Booking a routine appointment while urgent language is on the table is the
# specific failure we most want to catch: the agent treating an emergency as a
# scheduling task.

UrgentWithBookingRule = Rule(
  when_all=[
    UserMessageIsUrgent,
    IsWriteTool,
  ],
  description='Agent attempted a scheduling action in response to urgent symptom language',
)

WhenRules(
  rules_any=[UrgentWithBookingRule],
  then=[
    DeclareVerdict(verdict='escalate:urgent_mishandled'),
  ],
)
