Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

# The agent is an operations assistant. It schedules, it registers, it answers
# "where do I park". It does not practise medicine.
#
# The distinction that matters: a patient MENTIONING a symptom is normal and
# must not be blocked -- that is most of why people call a clinic. What gets
# blocked is the agent ANSWERING clinically instead of routing the question to
# someone qualified.

# --- The patient asked for a medical opinion ----------------------------------
#
# Denied at the point of asking, which is the only rule in this file that fires
# before the model has said anything. Two reasons it is worth having on top of
# the reply-side rules below:
#
#   1. Speed. Evaluated at the `input` stage, so the turn ends in ~15ms with a
#      safe reply instead of waiting out two CPU completions. The gate was never
#      the slow part; the model was, and this skips it entirely.
#   2. Certainty. The reply-side rules depend on the model drafting something
#      catchable. This one does not depend on the model at all -- it cannot be
#      talked round, and it holds even on a turn where the model would have
#      answered perfectly.
#
# It keys on the SHAPE of the request, never the symptom. "I've had a headache
# for days, can I see someone?" must reach the booking flow untouched; only
# "what should I take for this headache?" is refused. See
# lists/advice_request_phrases.yaml.
#
# Not escalated. Someone asking a scheduling bot for advice is doing something
# completely ordinary, and paging a human for it is how escalation queues become
# noise nobody reads.

ClinicalAdviceRequestedRule = Rule(
  when_all=[
    UserMessageRequestsAdvice,
  ],
  description=f'Patient asked for a medical opinion (matched: {UserMessageAdviceRequestTermStr})',
)

WhenRules(
  rules_any=[ClinicalAdviceRequestedRule],
  then=[
    DeclareVerdict(verdict='deny:clinical_advice_request'),
  ],
)

# --- The agent answered a clinical question -----------------------------------

ClinicalAnswerRule = Rule(
  when_all=[
    AgentReplyIsClinical,
    AgentDeferredToClinician == False,
  ],
  description=f'Agent produced clinical content without deferring (matched: {AgentReplyClinicalTermStr})',
)

WhenRules(
  rules_any=[ClinicalAnswerRule],
  then=[
    DeclareVerdict(verdict='deny:clinical_advice'),
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)

# --- Medication or dosage advice ----------------------------------------------
# Held separately and denied even when the agent hedged. "You could probably
# just take a couple of ibuprofen, but check with your doctor" is still a
# medication recommendation from a scheduling bot.

MedicationAdviceRule = Rule(
  when_all=[
    AgentReplyGivesMedicationAdvice,
  ],
  description=f'Agent produced medication or dosage advice (matched: {AgentReplyMedicationTermStr})',
)

WhenRules(
  rules_any=[MedicationAdviceRule],
  then=[
    DeclareVerdict(verdict='deny:medication_advice'),
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)

# --- Patient raised something clinical and the agent handled it cleanly -------
# Not a violation. Recorded so you can measure how often the boundary is
# approached, and confirm the agent is deferring rather than the rules simply
# never firing.

ClinicalQuestionDeferredRule = Rule(
  when_all=[
    UserMessageIsClinical,
    AgentDeferredToClinician == True,
  ],
  description=f'Patient raised a clinical topic ({UserMessageClinicalTermStr}); agent deferred correctly',
)

WhenRules(
  rules_any=[ClinicalQuestionDeferredRule],
  then=[
    DeclareVerdict(verdict='observe:clinical_deferred'),
  ],
)
