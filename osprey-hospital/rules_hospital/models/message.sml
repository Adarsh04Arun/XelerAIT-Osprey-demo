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

# SML will not interpolate an Optional[str] into an f-string, so narrow the
# matched phrases here once for the rule descriptions to use.
UserMessageClinicalTermStr: str = ForceString(s=UserMessageClinicalTerm)
AgentReplyClinicalTermStr: str = ForceString(s=AgentReplyClinicalTerm)
AgentReplyMedicationTermStr: str = ForceString(s=AgentReplyMedicationTerm)
UserMessageUrgentTermStr: str = ForceString(s=UserMessageUrgentTerm)
UserMessageSelfHarmTermStr: str = ForceString(s=UserMessageSelfHarmTerm)
AgentReplySelfHarmTermStr: str = ForceString(s=AgentReplySelfHarmTerm)
