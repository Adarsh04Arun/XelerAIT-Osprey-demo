Import(
  rules=[
    'models/base.sml',
  ],
)

# Scope containment works on three surfaces:
#
#   1. what the patient asked for   (UserMessage)
#   2. what the agent wants to say  (AgentReply)
#   3. what the agent wants to save (handled in models/onboarding.sml)
#
# A patient *asking* a clinical question is not a violation -- people do that
# constantly. The violation is the agent *answering* it. So we score the two
# surfaces separately and let the rules combine them.

UserMessageClinicalTerm: Optional[str] = PhraseListMatch(
  list='clinical_terms',
  text=UserMessage,
)

UserMessageIsClinical: bool = UserMessageClinicalTerm != None

# Is the patient asking for a medical OPINION, as opposed to mentioning a
# symptom on the way to booking? Keyed on the shape of the request rather than
# the topic -- see lists/advice_request_phrases.yaml for why that distinction
# carries the whole rule.
UserMessageAdviceRequestTerm: Optional[str] = PhraseListMatch(
  list='advice_request_phrases',
  text=UserMessage,
)

UserMessageRequestsAdvice: bool = UserMessageAdviceRequestTerm != None

AgentReplyClinicalTerm: Optional[str] = PhraseListMatch(
  list='clinical_terms',
  text=AgentReply,
)

AgentReplyIsClinical: bool = AgentReplyClinicalTerm != None

# Medication / dosage advice is its own bucket -- always out of scope,
# regardless of who raised it.
AgentReplyMedicationTerm: Optional[str] = PhraseListMatch(
  list='medication_terms',
  text=AgentReply,
)

AgentReplyGivesMedicationAdvice: bool = AgentReplyMedicationTerm != None

# Urgent/emergency language must never be handled conversationally -- it goes
# straight to a human, whichever surface it appears on.
UserMessageUrgentTerm: Optional[str] = PhraseListMatch(
  list='urgent_terms',
  text=UserMessage,
)

UserMessageIsUrgent: bool = UserMessageUrgentTerm != None

# Self-harm is scored on its own, not folded into UserMessageIsUrgent, because
# it needs a different reply, a higher escalation priority, and -- unlike every
# other rule in this ruleset -- it must fire regardless of how well the agent
# handled the turn. See rules/message/self_harm.sml.
UserMessageSelfHarmTerm: Optional[str] = PhraseListMatch(
  list='self_harm_terms',
  text=UserMessage,
)

UserMessageIndicatesSelfHarm: bool = UserMessageSelfHarmTerm != None

# Also scanned on the agent's own reply. If the model is the one raising it,
# something has gone badly wrong and a human needs to see the conversation.
AgentReplySelfHarmTerm: Optional[str] = PhraseListMatch(
  list='self_harm_terms',
  text=AgentReply,
)

AgentReplyMentionsSelfHarm: bool = AgentReplySelfHarmTerm != None

# Did the agent hedge and refer the patient onward? If so it stayed in scope
# even though clinical words appear in the reply.
AgentDeferredToClinician: bool = PhraseListMatch(
  list='deferral_phrases',
  text=AgentReply,
) != None

# --- Out-of-scope requests ----------------------------------------------------
#
# The outer boundary, as opposed to the clinical one. The agent handles
# appointments, registration, contact/insurance details, and questions about
# hours, location and parking. Everything else -- revenue, staff pay, bulk
# patient data, system internals, general chit-chat -- is refused flatly.
#
# Scored on both surfaces for the same reason the clinical rules are: the
# patient ASKING about revenue is not a violation, it is a reasonable thing for
# a confused caller to try. The violation is the agent answering, and an 8B
# model asked for a figure it does not have will invent one rather than decline.

UserMessageOutOfScopeTerm: Optional[str] = PhraseListMatch(
  list='out_of_scope_terms',
  text=UserMessage,
)

UserMessageIsOutOfScope: bool = UserMessageOutOfScopeTerm != None

# Attempts to override the instructions, hijack the persona, or read the prompt.
# Scanned on the patient's message only: "you are now booked in with Dr Okafor"
# is a good reply and contains "you are now".
UserMessageInjectionTerm: Optional[str] = PhraseListMatch(
  list='injection_attempt_phrases',
  text=UserMessage,
)

UserMessageIsInjectionAttempt: bool = UserMessageInjectionTerm != None

AgentReplyOutOfScopeTerm: Optional[str] = PhraseListMatch(
  list='out_of_scope_terms',
  text=AgentReply,
)

AgentReplyIsOutOfScope: bool = AgentReplyOutOfScopeTerm != None

# Did the agent refuse properly? Its own refusal necessarily repeats the
# subject -- "I can't give you revenue figures" contains "revenue" -- so
# without this escape the rule blocks the correct answer and substitutes a
# fallback for a reply that was already right. Separate list from
# deferral_phrases: declining to discuss finances is not evidence of declining
# to give medical advice, and one escape must not silently widen the other.
AgentRefusedOutOfScope: bool = PhraseListMatch(
  list='scope_refusal_phrases',
  text=AgentReply,
) != None

# SML will not interpolate an Optional[str] into an f-string, so narrow the
# matched phrases here once for the rule descriptions to use.
UserMessageClinicalTermStr: str = ForceString(s=UserMessageClinicalTerm)
AgentReplyClinicalTermStr: str = ForceString(s=AgentReplyClinicalTerm)
AgentReplyMedicationTermStr: str = ForceString(s=AgentReplyMedicationTerm)
UserMessageUrgentTermStr: str = ForceString(s=UserMessageUrgentTerm)
UserMessageSelfHarmTermStr: str = ForceString(s=UserMessageSelfHarmTerm)
AgentReplySelfHarmTermStr: str = ForceString(s=AgentReplySelfHarmTerm)
UserMessageOutOfScopeTermStr: str = ForceString(s=UserMessageOutOfScopeTerm)
AgentReplyOutOfScopeTermStr: str = ForceString(s=AgentReplyOutOfScopeTerm)
UserMessageAdviceRequestTermStr: str = ForceString(s=UserMessageAdviceRequestTerm)
UserMessageInjectionTermStr: str = ForceString(s=UserMessageInjectionTerm)
