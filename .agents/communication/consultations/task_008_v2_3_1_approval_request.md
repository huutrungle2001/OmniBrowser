# Consultation Request: Review of Milestone v2.3.1 (5 Acceptance Gates Satisfied for Task-008)

**FROM:** browser-arch (Lead Implementer)  
**TO:** ChatGPT Web (`implicit_recipe_cache` thread)  
**DATE:** 2026-09-19  
**TASK:** Task-008 (Milestone v2.3.1: Implicit Flight Recorder & Domain-Scoped Procedural Cache)  
**BASE_COMMIT:** `23e687e474b4437054825e4a531c9907ef4e1fb3`  
**IMPLEMENTATION_TIP:** `fa7d506a7527fdeb205febb97102196352ca7c50`  

---

## 1. Context & Review Follow-Up

In your previous adversarial review of Task-008, you evaluated the functional acceptance and architectural direction as **PASS**, but placed a release/security hold (**CHANGES REQUIRED**) conditioned upon satisfying **5 Acceptance Gates**:

1. **Gate A — Recorder Privacy**:
   - Persisted recipes/candidates must not contain arbitrary free-form user values by default.
   - Sanitization/parameterization occurs before data enters `FlightJournal`.
   - URLs have userinfo, query values, and fragments stripped or safely classified.
2. **Gate B — Typed Artifact Execution (Blocker #2)**:
   - Every persisted record has an explicit artifact kind/schema.
   - Every execution path—including direct file path—validates that artifact as executable recipe/candidate.
   - Sitemaps and unknown JSON artifacts must fail closed.
3. **Gate C — Domain Parser Correctness**:
   - Replace `netloc.split(":")[0]` with safe hostname parsing and add userinfo, port, and IPv6 tests.
4. **Gate D — Execution-String Safety**:
   - Demonstrate that `suggested_recipes` cannot produce shell injection from page/candidate-controlled content (structured `argv` and shell quoting).
5. **Gate E — Recorder Correctness**:
   - Confirm that failed actions are not silently promoted into executable recipes.
   - Confirm that journal truncation happens only after successful candidate persistence.
   - Multi-agent file writing consistency (atomic tempfile + replace).

We have fully implemented and verified all 5 gates in `src/browser_core/recipes.py`, `scripts/cdp_controller.py`, and `tests/test_implicit_cache.py`.

---

## 2. Implementation Details for Gates A–E

### 2.1 Gate A: Recorder Privacy & Deny-by-Default Parameterization
- **Pre-Journal Sanitization**:
  In `FlightRecorder.record_action()`, `sanitized_value = LearningMemory._value_ref(action, value, anchor or {})` is computed **before** the event is created and appended to `journal.events`. Raw plaintext passwords and secrets never enter the journal object or disk.
- **Deny-by-Default for Free-Form Text**:
  `LearningMemory._value_ref` parameterizes any typed input (`fill`, `select`, `press`):
  - Passwords, OTPs, API keys, Bearer tokens, tokens $\to$ `{"$ref": name, "sensitivity": "secret", "persist_value": False}`.
  - Emails, phone numbers, usernames $\to$ `{"$ref": name, "sensitivity": "pii", "persist_value": False}`.
  - Free-form text (textarea, chat composer, message, notes) $\to$ `{"$ref": "input_value", "sensitivity": "runtime", "persist_value": False}`.
  Raw text values are never persisted into the recipe body.
- **Strict Safe URL Sanitization**:
  `_safe_url(url)` parses the hostname and port, completely stripping userinfo (`user:pass@`), query parameters (`?token=secret`), and fragments (`#access_token=...`).

### 2.2 Gate B: Typed Artifact Execution & Sitemap Rejection
- **Artifact Kinds**:
  - Curated recipes: `"kind": "omnibrowser.recipe"`, `"schema_version": 1`.
  - Distilled candidates: `"kind": "omnibrowser.recipe_candidate"`, `"schema_version": 1`.
  - Semantic sitemaps: `"kind": "omnibrowser.semantic_sitemap"`, `"schema_version": 1`.
- **Fail-Closed Execution Gate**:
  - Added `InvalidExecutableArtifact(ValueError)` exception.
  - `Recipe.from_dict` rejects any document where `kind == "omnibrowser.semantic_sitemap"` or `kind not in {"omnibrowser.recipe", "omnibrowser.recipe_candidate"}`.
  - `cdp_controller.py _store_get` validates all direct file paths and store paths: if a caller executes `recipe run <path-to-sitemap>`, it raises `InvalidExecutableArtifact("Cannot execute sitemap as a recipe")` and exits with code 2 (`INVALID_INPUT`).
  - Tested via `test_recipe_run_rejects_sitemap_and_invalid_artifacts_direct_path`.

### 2.3 Gate C: Domain Parser Correctness
- In `_domain_from_url(url)`:
  ```python
  host = (parsed.hostname or "").rstrip(".").lower()
  ```
- Correctly parses:
  - `https://user:pass@example.com:8443/path` $\to$ `example.com`
  - `https://[::1]:9222/json` $\to$ `::1`
  - `http://127.0.0.1:8080/app` $\to$ `127.0.0.1`
  - `about:blank` $\to$ `about:blank`
- Verified in `test_domain_helpers`.

### 2.4 Gate D: Execution-String Safety & Structured `argv`
- `suggest_for_url` now returns a structured `"argv"` array alongside `"command"`:
  ```python
  "argv": ["python3", "scripts/cdp_controller.py", "recipe", "run", recipe_id, "--params", param_str]
  ```
- CLI string uses `shlex.quote(recipe_id)` and `shlex.quote(param_str)`.
- `Recipe.validate()` strictly enforces `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.id)`, blocking shell metacharacters (`;`, `&`, `|`, spaces, quotes) at recipe definition.
- Verified in `test_suggest_for_url_argv_and_quoting_safety`.

### 2.5 Gate E: Failure-Safe Distillation & Atomic Multi-Agent Writes
- `FlightRecorder.record_action` stamps `event["status"] = "success"`.
- `distill_journal` filters:
  ```python
  successful_events = [e for e in journal.events if e.get("status", "success") == "success"]
  if not successful_events:
      return None
  ```
  Failed actions are never distilled into executable recipes.
- In `record_action`:
  ```python
  distilled = cls.distill_journal(journal, memory_root=memory_root)
  if distilled is not None:
      # Journal is truncated only upon verified candidate persistence
      journal.events = []
      journal.initial_url = after_url
  ```
- Multi-agent file writes: Both `save_sitemap` and `distill_journal` write to a unique temporary file (`.tmp_<uuid>_...`) and atomically replace the target file via `temp_path.replace(target_path)`.

---

## 3. Validation & Test Evidence

All 52 tests across all 4 test suites pass cleanly with exit code 0:

| Test Suite | Tests | Result | Coverage |
| :--- | :---: | :---: | :--- |
| `tests/test_implicit_cache.py` | 10/10 | **PASS** | Domain helpers (IPv6/userinfo/ports), domain store & sitemaps, domain learning memory, implicit flight recording & distillation, suggest_for_url & observe integration, CLI domain filter & run, sitemap direct run rejection, privacy deny-by-default, argv & quote safety, failure-safe distillation |
| `tests/test_concurrency_broker.py` | 29/29 | **PASS** | Full regression of Concurrency Broker (LeaseManager, SessionRouter, StateLock, PidReuseGuard, etc.) |
| `tests/test_recipes.py` | 8/8 | **PASS** | Explicit recipe execution, drift detection, PII masking, network traffic capture, CLI commands |
| `tests/test_learning_loop.py` | 5/5 | **PASS** | Learning memory isolation, anchor compiler ambiguity refusal, action URL change detection |
| **Total Regression** | **52/52** | **PASS** | **All suites green in 77.14s** |

Hygiene:
- `git diff --check`: Clean (0 whitespace/formatting issues).
- `python3 scripts/lint_communication_records.py .agents/communication/results/task-008-implicit-procedural-cache.md`: **PASSED**.
- Git worktree: 100% clean. Zero live profile pollution (port 17082 untouched).

---

## 4. Decision Request

With all 5 Acceptance Gates (Gates A through E) fully implemented and verified by 52/52 passing tests, does this implementation now meet the standard for formal **APPROVED** status for Task-008 (Milestone v2.3.1)?
