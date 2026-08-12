Import(
  rules=[
    'models/base.sml',
  ],
)

# The model proposed a tool call without deciding anything.
#
# Observed, verbatim, in response to "what should I take for this headache?":
#
#     book_appointment(provider_id="null", start="null", department="null")
#
# Literal "null" strings, not JSON null, so nothing downstream treated them as
# missing. Every relevant rule fired -- unknown provider, unparseable start,
# outside clinic hours -- and each was true, and the set of them was useless:
#
#     UnknownProviderRule:     Provider null does not exist
#     UnparseableStartRule:    Could not interpret "null" as an appointment time
#     OutsideClinicHoursRule:  Requested start null ((unknown day)) is outside...
#
# Three denials describing the same non-event, none of them naming the actual
# problem, and the wording actively misleading: nobody hallucinated a clinician
# called "null". The finding was that the model had proposed a booking with no
# content in it at all.
#
# So this fires FIRST and says the true thing once. The other rules still run --
# nothing is suppressed, and the ruleset stays a flat set of independent
# comparisons rather than a chain with an order -- but the gateway ranks
# malformed_tool_call above them when it explains itself to the model, which
# gives it one correctable instruction instead of three contradictory ones.
#
# Deliberately NOT escalated. A confused tool call is the model being an 8B
# model, not a safety event, and it is already caught. Escalating it would page
# a human several times an hour for something the gate handles perfectly well
# on its own -- and an escalation queue that cries wolf is one nobody reads
# when the self-harm alert lands.

MalformedToolCallRule = Rule(
  when_all=[
    CallHasUsableArgs == False,
  ],
  description=f'Agent proposed {ToolNameStr} with no usable arguments -- all {CallNullLikeArgCount} were empty or placeholder values',
)

WhenRules(
  rules_any=[MalformedToolCallRule],
  then=[
    DeclareVerdict(verdict='deny:malformed_tool_call'),
  ],
)
