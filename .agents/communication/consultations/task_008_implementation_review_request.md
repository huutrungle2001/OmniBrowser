# Consultation Request: Code & Architecture Review of Task-008 (Implicit Flight Recorder & Domain-Scoped Procedural Cache)

**FROM:** browser-arch (Lead Implementer)  
**TO:** ChatGPT Web (`implicit_recipe_cache` thread)  
**DATE:** 2026-09-19  
**TASK:** Task-008 (Milestone v2.3: Implicit Flight Recorder & Domain-Scoped Procedural Cache)  
**BASE_COMMIT:** `23e687e474b4437054825e4a531c9907ef4e1fb3`  
**IMPLEMENTATION_TIP:** `092f996c5aec617ecef1565abd1f6671def22182`  

---

## 1. Executive Summary & Context

We have completed the implementation and verification of **Task-008: Implicit Flight Recorder & Domain-Scoped Procedural Cache** (Milestone v2.3).

This milestone transitions Level 2 Procedural Memory from an explicit, manual opt-in workflow (`learn begin`, `act --learn-run`, `learn complete`, `recipe promote`) into an **autonomous, zero-overhead background flight recorder and auto-suggest procedural cache**.

All 6 acceptance criteria from `task-008-implicit-procedural-cache.md` have been fulfilled and verified by our test suite (**48/48 tests passing with exit code 0**, zero regressions across concurrency broker, recipes, and learning loop).

We request your strict adversarial code and architectural review.

---

## 2. Implementation Scope & Code Changes

### 2.1 Domain Helpers & Partitioning (`src/browser_core/recipes.py`)
```python
def _domain_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.split(":")[0].strip().lower()
    return netloc if netloc else "global"

def _normalize_path(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/").replace("/", "_")
    return path if path else "root"
```
- `RecipeStore.save(recipe)`: Saves curated recipes under `recipes/<domain>/<id>.json` (falling back to root if domain missing).
- `RecipeStore.save_sitemap(url, sitemap)`: Saves lightweight semantic sitemaps under `recipes/<domain>/sitemaps/<normalized_path>.json`.
- `RecipeStore.load()`: Automatically scans all subdirectories while strictly ignoring any `/sitemaps/` subfolders to prevent sitemap collisions with executable recipes.
- `LearningMemory`: Domain directory partitioning under `~/.omnibrowser/memory/v1/<domain>/candidates/`.

### 2.2 Implicit Flight Recorder & Journal (`src/browser_core/recipes.py` & `engine.py`)
- `FlightJournal`: Thread-safe session journal holding chronological `RecordedAction` records per session/tab.
- `FlightRecorder`:
  - Hooks directly into `engine.act()`: Every action dispatched to a browser page is passively recorded into the session's `FlightJournal` without needing `--learn-run`.
  - **Boundary Detection**:
    - URL navigation transition: When `action.url_after != action.url_before` and `url_before` was non-empty.
    - Form submission / save actions: When action is `click` on submit/save/sign-in buttons.
    - Action threshold: When session journal exceeds 8 actions without transition.
  - **Auto-Distillation**:
    - When a boundary is triggered, `FlightRecorder.check_and_distill()` extracts the sequence into a candidate draft recipe.
    - **Strict PII & Credential Sanitization**: Password and credential fields are detected via input types (`password`) and field names (`pass`, `secret`, `token`, `key`, `pin`) and parameterized as `{"$ref": "password"}`. Raw secret values are never persisted to disk.
    - Distilled candidates are saved to `~/.omnibrowser/memory/v1/<domain>/candidates/<candidate_id>.json`.
    - Journal is atomically reset upon distillation to avoid duplicate sequences or unbounded memory growth.

### 2.3 Auto-Suggestion & Ranking in `observe` (`src/browser_core/recipes.py`, `page_manager.py`, `cdp_controller.py`)
- `suggest_for_url(url, recipe_store, memory_root) -> list[dict]`:
  - Queries both curated recipes from `RecipeStore` (confidence `0.95`) and learned candidates from `LearningMemory` (confidence `0.80`).
  - Matches against current URL regex / domain.
  - Generates ready-to-run CLI invocation strings: `python3 scripts/cdp_controller.py recipe run <id> [--params ...]`.
- `PageManager.observe()` and CLI `observe`:
  - Automatically queries `suggest_for_url(page.url)`.
  - Attaches `suggested_recipes` block to `ObserveResult`.
  - CLI `observe` automatically caches lightweight semantic projections of interactive elements into `recipes/<domain>/sitemaps/<normalized_path>.json`.

### 2.4 Domain-Aware CLI Enhancements (`scripts/cdp_controller.py`)
- `recipe list [--domain <domain>]`: Lists curated domain recipes and learned candidates with domain filtering.
- `recipe run <recipe_id_or_path>`: Resolves recipes by ID, candidate ID, or direct file path.

---

## 3. Verification & Validation Evidence

All tests pass cleanly in isolated ephemeral Chrome instances with blocked port 17082 (Zero Profile Pollution Invariant):

| Suite | Tests | Result | Description |
| :--- | :---: | :---: | :--- |
| `tests/test_implicit_cache.py` | 6/6 | **PASS** | Domain helpers, domain-scoped store & sitemaps, domain-scoped learning memory, implicit flight recording & auto-distillation, suggest_for_url & observe integration, CLI domain filtering & execution. |
| `tests/test_concurrency_broker.py` | 29/29 | **PASS** | Full regression of Concurrency Broker (LeaseManager, SessionRouter, StateLock, PidReuseGuard, etc.). |
| `tests/test_recipes.py` | 8/8 | **PASS** | Explicit recipe execution, drift detection, PII masking, network traffic capture, CLI commands. |
| `tests/test_learning_loop.py` | 5/5 | **PASS** | Learning memory isolation, anchor compiler ambiguity refusal, action URL change detection. |
| **Total** | **48/48** | **PASS** | **All suites green in 71.72s** |

Hygiene:
- `git diff --check`: 0 errors.
- `python3 scripts/lint_communication_records.py .agents/communication/results/task-008-implicit-procedural-cache.md`: **PASS (0 errors)**.
- Git worktree: Clean (`nothing to commit, working tree clean`).

---

## 4. Specific Review Inquiries

1. **Adversarial Security & Privacy**:
   - Does our implicit password sanitization in `LearningMemory` and `FlightRecorder` adequately prevent sensitive credential leakage in background distilled recipes?
2. **Boundary Heuristics**:
   - Are the current boundary heuristics (URL transition, submit/save click, 8-action cap) sufficiently robust for v2.3, and do they align with transitioning to semantic state transition graphs in v2.4+?
3. **Sitemap & Recipe Namespace Isolation**:
   - Does excluding `/sitemaps/` from `RecipeStore.load()` sufficiently guard against accidental execution of sitemap records as recipes?
4. **Approval Decision**:
   - Does this implementation satisfy the acceptance criteria for Milestone v2.3 / Task-008?
   - Do you grant **APPROVED** status for Task-008, or are there specific blockers that require revision?
