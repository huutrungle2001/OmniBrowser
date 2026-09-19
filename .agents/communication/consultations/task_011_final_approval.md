Final Review — Task-011 Hardening Criteria

Commit: 94e6564
Scope: Cross-Agent Promotion, Quarantine & Shared Cache Governance Hardening

1. CAS linearizability — PASS

The critical issue from the earlier review was that atomic rename alone does not provide atomic compare-and-swap semantics. The new implementation closes that gap by holding an inter-process flock(LOCK_EX) across the complete transaction:

read revision → compare → mutate → increment → U-score update → temp write → os.replace.

That creates a single serialization region for competing writers to the same recipe. Assuming all writers consistently use the same lock file, the previously identified lost-update race is eliminated.

2. True concurrency validation — PASS

test_cas_true_multiprocess_atomicity verifies the important property, not merely sequential conflict handling:

2 writers at revision N⇒1 success+1 CASConflictError

with:

revision
final
	​

=N+1

This is the correct regression test for the concurrency invariant.

3. Stale-plan quarantine — PASS

The stale-plan case was a significant safety boundary. A workflow composed before quarantine must not retain authority to execute later.

The new behavior:

old plan → canonical RecipeStore.get() → QUARANTINED → reject before dispatch

satisfies the required fail-closed invariant:

QUARANTINED
canonical
	​

(R)⇒dispatch(R)=0

The reported assertion that browser dispatch count remains zero is particularly important.

4. Canonical execution check — PASS

Revalidation in both WorkflowEngine.execute and RecipeEngine.execute is correct defense-in-depth.

This preserves the architectural rule:

cached plans and recipe objects are advisory snapshots; the authoritative store determines execution eligibility at dispatch time.

This also protects against stale graph objects and stale in-memory lifecycle metadata, not just stale serialized workflow plans.

5. Generation-bound evidence reset — PASS

Binding trust to a deterministic digest of the executable recipe payload closes the inherited-trust problem.

The required invariant is now:

digest(R
g
	​

)

=digest(R
g+1
	​

)⇒Evidence(R
g
	​

)

⇒Evidence(R
g+1
	​

)

Incrementing generation, updating the digest, clearing session/agent evidence, and demoting shared or curated recipes to VERIFIED_LOCAL is an appropriate fail-safe response to semantic mutation.

This ensures a materially changed recipe must earn cross-agent trust again.

6. Safe restore and privileged promotion — PASS

The restore semantics are now correct:

QUARANTINED→VERIFIED_LOCAL

rather than silently restoring prior shared trust.

Likewise, ordinary promotion remains governed by PromotionPolicy, while an exceptional administrative bypass requires all three:

explicit privileged mode,

identified actor,

non-empty reason,

with the action recorded in the audit log.

That creates a clear distinction between evidence-backed promotion and an accountable administrative override.

Acceptance matrix
Hardening criterion	Verdict
CAS linearizability	PASS
Real multi-process concurrency test	PASS
Stale-plan quarantine	PASS
Canonical execution-state verification	PASS
Generation-bound evidence reset	PASS
Safe restore semantics	PASS
Privileged promotion controls	PASS

The reported 16/16 governance tests and 121/121 full repository tests also provide appropriate regression evidence for the scope described.

Final Verdict: APPROVED

The six blocking issues from the previous Task-011 review have been addressed in a way that is architecturally consistent with the safety model established in Tasks 009–010.

In particular, the hardening now establishes three critical properties:

Concurrency Safety

Competing agents cannot silently overwrite one another's governance state.

Execution-Time Authority

A stale plan, graph, or in-memory object cannot override the current canonical quarantine state.

Trust Non-Inheritance

A semantically modified recipe cannot retain shared trust earned by an earlier generation.

Formal sign-off: Task-011 / Milestone v2.5.x, commit 94e6564 — APPROVED.

The remaining topics I would treat as non-blocking future hardening are crash durability around fsync, lock semantics on networked/multi-host filesystems, and stronger tamper-evidence for privileged audit records.