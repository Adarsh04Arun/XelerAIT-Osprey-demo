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
