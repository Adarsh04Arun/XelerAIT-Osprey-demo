Import(
  rules=[
    'models/base.sml',
    'models/message.sml',
  ],
)

# The outer boundary of what this agent is for.
#
# rules/message/clinical_scope.sml polices the one subject the agent must never
# opine on. This file polices everything it was never given a mandate over:
# the practice's finances, staff pay, other patients in bulk, system internals,
# and general questions that have nothing to do with a patient's own care
# arrangements.
#
# Why this needs to be a rule rather than a line in the system prompt: the
# README already records the model calling update_patient_record in response to
# a question about parking. Nothing in that prompt invited a write. A model that
# will edit a chart during a parking question will also answer a revenue
# question, and it will not answer it with "I don't know" -- an 8B model asked
# for last quarter's figures produces a confident number. A plausible invented
# figure attributed to the practice is materially worse than silence, and it is
# the kind of error that looks completely correct in a transcript.
#
# The response is deliberately flat: `deny:out_of_scope`, which the gateway
# turns into "that request cannot be processed". No partial answer, no pointer
# to where the data might live, no restating of the question.

# --- The patient asked for something off-domain -------------------------------
#
# Denied at the point of asking rather than only on the reply, so the model
# never gets a turn in which answering is possible. Not escalated: someone
# asking a scheduling bot about opening revenue is confused, not hostile, and
# paging a human for it would be exactly the reviewer-overload failure that
# makes escalation queues get ignored.

OutOfScopeRequestRule = Rule(
  when_all=[
    UserMessageIsOutOfScope,
  ],
  description=f'Request is outside the remit of this agent (matched: {UserMessageOutOfScopeTermStr})',
)

WhenRules(
  rules_any=[OutOfScopeRequestRule],
  then=[
    DeclareVerdict(verdict='deny:out_of_scope'),
  ],
)

# --- The agent answered anyway ------------------------------------------------
#
# The reply-side guard, and the one that actually catches confabulation: the
# question may be phrased in a way the list misses ("how did the practice do
# financially in Q3") while the answer contains the figures regardless.
#
# Escalated, unlike the request-side rule. The patient asking is a mistake; the
# agent answering is the agent doing something it has no basis for, which is
# the same category as a scope violation and wants a human to read it.
#
# AgentRefusedOutOfScope is the necessary escape. A correct refusal repeats the
# subject -- "I can't give you revenue figures" contains "revenue" -- so without
# it this rule would block the right answer and replace it with a fallback,
# recording a violation for behaviour that was already correct.

AgentAnsweredOutOfScopeRule = Rule(
  when_all=[
    AgentReplyIsOutOfScope,
    AgentRefusedOutOfScope == False,
  ],
  description=f'Agent produced content outside its remit without refusing (matched: {AgentReplyOutOfScopeTermStr})',
)

WhenRules(
  rules_any=[AgentAnsweredOutOfScopeRule],
  then=[
    DeclareVerdict(verdict='deny:out_of_scope'),
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)

# --- The agent tried to act on an out-of-scope request -------------------------
#
# "Show me all patients" answered with a lookup_appointments call, or a revenue
# question answered by writing something to a record. The tool gate will
# usually deny these on their own merits, but the combination is worth naming
# separately: it is the model treating an off-domain request as a task to
# complete rather than one to decline, which is the same failure shape as
# booking an appointment in response to chest pain.

OutOfScopeWithToolCallRule = Rule(
  when_all=[
    UserMessageIsOutOfScope,
    IsWriteTool,
  ],
  description='Agent attempted a write in response to an out-of-scope request',
)

WhenRules(
  rules_any=[OutOfScopeWithToolCallRule],
  then=[
    DeclareVerdict(verdict='escalate:scope_violation'),
  ],
)

# --- Observed: refused correctly ----------------------------------------------
# Recorded so the refusal rate is measurable. Without it you cannot tell a
# ruleset that is working from one whose lists never match anything -- the same
# reason observe:clinical_deferred exists.

OutOfScopeRefusedRule = Rule(
  when_all=[
    UserMessageIsOutOfScope,
    AgentRefusedOutOfScope == True,
  ],
  description=f'Patient asked something off-domain ({UserMessageOutOfScopeTermStr}); agent refused correctly',
)

WhenRules(
  rules_any=[OutOfScopeRefusedRule],
  then=[
    DeclareVerdict(verdict='observe:out_of_scope_refused'),
  ],
)
