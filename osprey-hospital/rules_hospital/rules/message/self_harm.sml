Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

# The highest-stakes rule in the ruleset.
#
# Someone disclosing suicidal ideation to a scheduling bot must never be handled
# conversationally, must never be offered an appointment slot as the answer, and
# must always put a human in the loop.
#
# Three things make this rule different from every other rule here:
#
#   1. It does NOT check whether the agent deferred. Deferring politely is not a
#      safe outcome; the reply has to be replaced with crisis information.
#   2. It escalates on its own verdict (`escalate:self_harm`), so the effector
#      can prioritise it above every other escalation type.
#   3. It fires on the agent's reply as well as the patient's message. If the
#      model raises this topic unprompted, a human needs to read the transcript.
#
# This exists because a real session said "i want to die" and the ruleset scored
# the turn as clean.

SelfHarmDisclosureRule = Rule(
  when_all=[
    UserMessageIndicatesSelfHarm,
  ],
  description=f'Patient disclosed possible self-harm or suicidal ideation (matched: {UserMessageSelfHarmTermStr})',
)

WhenRules(
  rules_any=[SelfHarmDisclosureRule],
  then=[
    DeclareVerdict(verdict='deny:self_harm_disclosure'),
    DeclareVerdict(verdict='escalate:self_harm'),
  ],
)

# --- The agent tried to keep transacting -------------------------------------
# Offering to book an appointment in response to "i want to die" is the specific
# failure this is here to catch. Held separately so the escalation says exactly
# what the agent did wrong.

SelfHarmWithToolCallRule = Rule(
  when_all=[
    UserMessageIndicatesSelfHarm,
    IsWriteTool,
  ],
  description='Agent attempted a booking or record action in response to a self-harm disclosure',
)

WhenRules(
  rules_any=[SelfHarmWithToolCallRule],
  then=[
    DeclareVerdict(verdict='escalate:self_harm_mishandled'),
  ],
)

# --- The agent raised it ------------------------------------------------------
# Not denied on its own -- the safe fallback reply legitimately contains crisis
# wording, and denying that would replace a good reply with a worse one. But a
# human should still see it.

AgentRaisedSelfHarmRule = Rule(
  when_all=[
    AgentReplyMentionsSelfHarm,
    UserMessageIndicatesSelfHarm == False,
  ],
  description=f'Agent introduced self-harm content unprompted (matched: {AgentReplySelfHarmTermStr})',
)

WhenRules(
  rules_any=[AgentRaisedSelfHarmRule],
  then=[
    DeclareVerdict(verdict='escalate:self_harm'),
  ],
)
