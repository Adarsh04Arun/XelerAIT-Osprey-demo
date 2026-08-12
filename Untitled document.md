## **1\. Workflow-level failures (beyond the prompt)**

**Indirect prompt injection.** An agent retrieves a document, email, or web page containing instructions ("ignore prior rules, wire funds to…"). The user's prompt was clean; the payload arrived mid-workflow. 

*Guardrails:* treat all tool output as untrusted data, not instructions; evaluate tool returns through the policy engine the same way prompts are; strip/quarantine imperative content from retrieved text; require a second, independent confirmation for any action whose trigger originated in retrieved content.

**Confused deputy / privilege inheritance.** An agent runs with a service account broader than the requesting user's entitlements, so a low-privilege user gets high-privilege outcomes. 

*Guardrails:* propagate the end user's identity through every hop rather than a shared service principal; enforce least-privilege scoped tokens per tool; deny-by-default on any action the initiating human couldn't perform themselves.

**Runaway loops and cost blowouts.** Two agents ping-pong a task, or a retry path recurses. At machine speed this burns budget or hammers a downstream API before anyone notices. 

*Guardrails:* hard caps on steps, depth, wall-clock time, and spend per session; circuit breakers on repeated identical tool calls; rate limits per agent identity, not just per application.

## **2\. Policy staleness and inconsistency**

**Partial policy propagation.** A threshold is tightened centrally, but one team's agent caches the old config or runs an older SDK — the article's "rule that updates in some places and not others." 

*Guardrails:* versioned policy with a single source of truth; agents fetch policy at decision time, not at deploy time; refuse to act if the policy version is older than N minutes; a dashboard reconciliation showing which agents are on which policy version.

**Silent model or tool substitution.** A vendor updates the underlying model, or an MCP tool's description changes, and behavior shifts without any code change on your side. *Guardrails:* pin model and tool versions; behavioral regression suites run on every version bump; hash tool definitions and alert on drift; treat a changed tool description as a change requiring re-approval.

## **3\. System-level (multi-agent) patterns**

**Structuring / threshold probing.** Individually compliant actions that only violate policy when summed — the article's transfer example, but equally: five "read-only" queries that together reconstruct a restricted dataset. 

*Guardrails:* stateful session-level aggregation, not per-transaction checks; cumulative limits across a session, a customer, and a time window; anomaly detection on approach-the-limit patterns.

**Consensus collapse / echo chambers.** Agents reinforce each other's flawed reasoning because they share a model, a prompt lineage, or a retrieved context — apparent agreement that's really correlated error. 

*Guardrails:* deliberately heterogeneous reviewer agents (different models, different objectives, as in the article's fraud/revenue/CX/risk panel); require independent evidence citations per agent; flag unanimity reached without any dissent or new evidence as suspicious, not reassuring.

**Deadlock and no-quorum.** A split panel resolves itself by default rather than escalating. 

*Guardrails:* explicit quorum rules; a tie routes to human review automatically; never let "no blocker" mean "approved."

**Orchestration drift.** Dynamic routing takes a path nobody tested — an agent discovers a tool combination that technically works but was never risk-assessed. 

*Guardrails:* allowlist agent-to-agent and agent-to-tool edges; alert on any first-seen path in production; sandbox new paths before enabling them.

## **4\. Human-in-the-loop failure modes**

**Reviewer overload → rubber stamping.** Escalate too much and humans approve reflexively; the control exists on paper only. *Guardrails:* tune escalation rates and monitor them as a health metric; track approval latency and approve-rate per reviewer as a rubber-stamp signal; require a reason code on override; sample-audit approved decisions.

**Escalation without context, or with nobody home.** A judgment call lands in a queue at 2 a.m. with a timeout that defaults to allow.

*Guardrails:* default-deny on timeout for consequential actions; assemble evidence, votes, and applicable policy into the review item (the article's point); on-call routing with SLA per risk tier.

## **5\. Attribution and audit gaps**

**Untraceable handoffs / shadow agents.** An agent spun up by a business team with no registered identity acts inside a governed workflow; logs show events but not who. 

*Guardrails:* mandatory agent identity registry; no credential issuance without a registered owner, purpose, and expiry; reject unsigned agent-to-agent messages; egress monitoring to detect unregistered callers.

**Non-reproducible decisions.** A regulator asks why a decision was made and you can't reconstruct it — the model, prompt, retrieved context, and policy version have all moved on. 

*Guardrails:* immutable, tamper-evident logs capturing model version, prompt, retrieved context hashes, tool calls, policy version, and outcome; retention aligned to regulatory windows; replay capability for a given decision ID.

**Cross-context data leakage.** Shared memory or context passed between agents carries one customer's or tenant's data into another's session. 

*Guardrails:* per-session context isolation with explicit allowlists for what may cross an agent boundary; DLP checks on agent-to-agent messages, not just on outbound customer responses; tenant tags enforced at the memory layer.

---

A cross-cutting one worth flagging: **the governance layer itself is a single point of failure.** If the policy engine is down or latency-degraded, agents either stall or fail open. 

*Guardrail:* Define fail-closed behavior explicitly per risk tier, with a degraded mode that permits only a pre-approved low-risk action set — and monitor the monitor.

