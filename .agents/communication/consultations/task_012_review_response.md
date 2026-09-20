Formal Architecture Review — Task-012 / Milestone v2.6

Commit: 8fd4ae2
Scope: Self-Healing Anchor Bundles, Deterministic Local Repair & STG Alternate Route Discovery
Reported validation: 7/7 self-healing tests passed; 128/128 repository tests passed; zero live-profile pollution.

Final Verdict: CHANGES REQUIRED

The architecture is strong overall, especially the separation between deterministic online resolution and governed offline learning. However, the current design description still contains safety gaps that are material enough to block approval.

1. Dual-plane online resolution vs offline repair promotion
Verdict: PASS, with one required hardening

The split is architecturally correct:

ONLINE
resolve from existing candidate bundle
→ execute only against current recipe semantics
→ record RepairCandidate
→ never modify canonical recipe

OFFLINE
review/apply RepairCandidate
→ CAS-protected canonical mutation
→ generation increment
→ evidence reset
→ audit record

This preserves the key invariant:

OnlineHealing

⇒CanonicalMutation

That is exactly the correct boundary for procedural memory in a multi-agent environment.

The reported byte-for-byte test proving that online healing leaves canonical recipe content, revision, and generation unchanged is particularly valuable.

Remaining requirement

A RepairCandidate should be bound to the exact recipe version that generated it:

recipe_id
recipe_generation
recipe_content_digest
step_index
...

Then:

digest
repair
	​


=digest
canonical
	​

⇒reject repair

Otherwise an old repair candidate may be applied to a newer recipe generation whose step semantics have changed.

So the dual-plane model itself passes; stale-repair protection remains required.

2. Candidate ambiguity disqualification
Verdict: PASS as an ambiguity rule, INSUFFICIENT as the full mutation-safety rule

This invariant is correct:

count == 0 → try next candidate
count > 1  → reject candidate
count == 1 → candidate is unambiguous

And specifically:

never use .first() on an ambiguous mutating target

should remain a hard invariant.

However:

Unique

=Correct

A uniquely matched element may still be semantically wrong.

For example, a text-only anchor with prior confidence 0.60 could uniquely match a destructive control on the wrong entity or panel.

Therefore count == 1 should establish only:

Uniqueness

not:

AuthorizationToMutate
Required R3/R4 rule

High-risk mutation needs risk-sensitive acceptance.

For example:

Risk class	Automatic fallback expectation
R0–R1	Unique deterministic candidates broadly acceptable
R2	Require stronger contextual/semantic evidence
R3	High-confidence semantic candidate only
R4	Highest-confidence candidate plus existing irreversible-action gate

For R3/R4, low-confidence candidates such as:

text-only;

generic neighborhood anchor;

weak generic CSS;

should not be sufficient solely because their count is 1.

This must sit in addition to, not instead of, the existing JIT write barrier.

3. STG alternate transition discovery and circular-state safety
Verdict: CHANGES REQUIRED

There are two separate issues.

A. UNKNOWN_SIDE_EFFECT must never enter detour routing

This is the most important blocker.

Task-010 established:

UNKNOWN_SIDE_EFFECT⇒immediate halt

Task-012 must preserve that invariant.

A detour is safe only if the failed transition is known to be side-effect-free, for example:

ANCHOR_NOT_FOUND
AMBIGUOUS_TARGET
PRECONDITION_FAILED
SAFE_FAILURE

where the engine knows browser mutation did not occur.

But:

UNKNOWN_SIDE_EFFECT

means the operation may already have occurred.

Therefore the only safe behavior is:

UNKNOWN_SIDE_EFFECT
→ halt workflow
→ no alternate route
→ no retry
→ no next edge
→ quarantine if applicable

Formally:

Outcome(e
k
	​

)=UNKNOWN_SIDE_EFFECT⇒∀j>k, Dispatch(e
j
	​

)=0

If the current implementation genuinely detours after an unknown side effect, that is a direct regression against the previously approved safety contract.

B. Dynamic replanning needs workflow-global loop protection

A single Dijkstra path with positive costs will not intentionally contain a beneficial cycle.

But repeated replanning can still loop globally.

Example:

A → B → D
      X

replan:
B → C → A → ...

or successive plans can reintroduce previously failed edges unless exclusions persist.

The workflow therefore needs monotonic execution history such as:

FailedEdges
t+1
	​

=FailedEdges
t
	​

∪{newFailedEdge}

plus bounded recovery controls:

workflow_failed_edges
visited_transition_attempts
max_detours
max_total_edges

The original execution risk envelope must also survive replanning:

Risk(detour)≤maxAllowedRisk(originalWorkflow)

and an R4 edge must never appear merely because it offers the cheapest alternate route when R4 was not authorized.

So alternate routing is architecturally valid, but only for known-safe failures and under workflow-global history/risk constraints.

4. Overall architectural correctness and verdict

The strong parts of v2.6 are clear:

Area	Assessment
Anchor bundle abstraction	PASS
Ordered deterministic resolver	PASS
Ambiguous candidate rejection	PASS
Zero online canonical mutation	PASS
Repair ledger concept	PASS
Offline governed repair application	PASS
Generation reset after applied repair	PASS
Regression suite	128/128 PASS
Live-profile isolation	PASS

The blocking gaps are:

Safety property	Status
R3/R4 confidence-aware fallback	NOT ESTABLISHED
Absolute halt on UNKNOWN_SIDE_EFFECT	FAIL from current design description
Detour risk-envelope preservation	NOT ESTABLISHED
Cross-replan loop prevention	NOT ESTABLISHED
RepairCandidate generation/digest binding	NOT ESTABLISHED
Required acceptance criteria for approval
1. UNKNOWN_SIDE_EFFECT is terminal.
   No detour, retry, or later edge dispatch.

2. R3/R4 fallback is risk-aware.
   A unique low-confidence anchor is not sufficient
   authority for persistent/irreversible mutation.

3. Alternate routes inherit the original policy envelope.
   max-risk, R4 authorization, quarantine state,
   and canonical lifecycle checks must be preserved.

4. Replanning keeps workflow-global history.
   Failed-edge exclusions must accumulate and detours
   must have a finite execution/replan budget.

5. RepairCandidate is generation/content bound.
   Applying a repair captured from an older recipe
   generation must fail closed.

6. Add explicit negative regression tests for all above.
Formal sign-off

Task-012 / Milestone v2.6 — CHANGES REQUIRED

The self-healing architecture is fundamentally sound and does not require redesign. The online/offline separation, zero canonical mutation during live healing, deterministic candidate bundles, and governed offline repair are all strong design decisions.

Approval is blocked specifically by execution-safety preservation: UNKNOWN_SIDE_EFFECT must remain an absolute workflow stop; high-risk R3/R4 mutation cannot rely on uniqueness alone; dynamic detours must preserve the original authorization envelope and maintain global failed-edge/history state; and offline repair evidence must be version-bound.

Once these conditions are implemented and regression-tested, Task-012 should be eligible for APPROVED without changing its core architecture.