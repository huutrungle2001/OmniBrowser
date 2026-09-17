# OmniBrowser v1.1 Telemetry Benchmark Report

Date: 2026-09-17

## Executive result

The v1.1 battle-testing suite completed successfully: **9 passed** in **11.24 s**.
The run used local fixtures and an ephemeral Chromium profile with a dynamically
allocated CDP port. No live profile port was used.

The end-to-end CLI subprocess benchmark used six samples: three `observe` calls
and three first-action `act` calls. Mixed latency was **204.770 ms p50** and
**438.890 ms p95**, both below the 400 ms / 900 ms targets. The three first
actions succeeded (**3/3 = 100%**). The augmented raw DOM measured **146,761
bytes** and the largest emitted semantic payload measured **1,432 bytes**, a
**99.0243% byte reduction**.

## Measured results

| Metric | Measured result | Target | Status |
| --- | ---: | ---: | --- |
| End-to-end CLI subprocess latency p50 (6 samples) | 204.770 ms | <400 ms | Pass |
| End-to-end CLI subprocess latency p95 (6 samples) | 438.890 ms | <900 ms | Pass |
| Raw DOM capture | 146,761 bytes | Informational | — |
| Largest semantic observation payload | 1,432 bytes | <15,360 bytes | Pass |
| Byte reduction (raw DOM vs semantic max) | 99.0243% | >80% | Pass |
| First workflow action success | 3/3 (100%) | >90% | Pass |
| Workflow resolution at Level 1 | 2/3 (66.7%) | Informational | — |

The latency figures above are measured at the real CLI subprocess boundary and
include CLI startup/import, CDP attach, the operation, JSON output, and teardown.
These are distinct from direct core/primitive timings emitted by the workflow
tests and must not be presented as controller-boundary timings.

### Payload and token comparison

The benchmark directly captured both representations from the augmented test
page. The raw value is `document.documentElement.outerHTML` and the semantic
value is the CLI's compact JSON response. The benchmark's raw-CDP fast observe
path measured **8.936 ms** and was explicitly test-verified; this is reported for
context and is not included in the six subprocess latency samples. The transport
uses this robust fast path where available, with a Playwright fallback for
connection and page operations. Using the report convention of approximately
four bytes per token:

| Representation | Bytes | Approx. tokens |
| --- | ---: | ---: |
| Augmented raw DOM capture (observed) | 146,761 | approximately 36,690 |
| OmniBrowser semantic projection (largest observed) | 1,432 | approximately 358 |

The measured byte reduction is:

`1 - 1,432 / 146,761 = 99.0243%`.

The token figures are estimates, since this repository does not include a
tokenizer dependency; exact model-token counts will vary by tokenizer and JSON
content.

## Workflow evidence

### 1. Semantic form

The test completed validation handling, `fill`, `select`, `check`, and submit
through opaque semantic references. The observed payload was 1,567 bytes. All
six Level 1 operations succeeded; the validation branch produced the expected
message before the successful submission (`Submitted: Ada Lovelace`).

### 2. Dynamic SPA

The client-side render produced a state delta of **2 added, 1 changed, and 1
removed** node. The old reference was safely recovered through a unique
fingerprint match after the client-side replacement; the recovered fill
succeeded. Observed payloads were 1,565 bytes before mutation and 1,727 bytes
after recovery.

### 3. Canvas and blocker escalation

The non-semantic canvas workflow used Level 3 `runBrowserCode` to draw a bounded
canvas and Level 4 `inspectVisual` for a 559-byte targeted crop. A CAPTCHA page
returned `requires_human_verification: true`; the test explicitly confirmed
that no automated bypass was attempted.

### Escalation breakdown

By emitted workflow operation events (14 total):

| Level | Events | Share |
| --- | ---: | ---: |
| Level 1 `observe`/`act` | 11 | 78.6% |
| Level 3 `runBrowserCode` | 1 | 7.1% |
| Level 4 `inspectVisual` | 2 | 14.3% |

By workflow turn, two workflows resolved entirely at Level 1 and one required
Level 3/4 escalation. This is **2/3 Level 1 turns (66.7%)**; the event-level
breakdown above is provided because one workflow contains multiple operations.

## Reproduction and safety

From the repository root, run:

```bash
python3 -m pytest tests/test_v1_1_battle_testing.py -v -s
```

The captured canonical run reported `9 passed in 11.24s` and emitted telemetry for the
three workflows plus the controller-boundary benchmark. The fixture setup
launches Chromium with
`--remote-debugging-port=0` and an isolated temporary `--user-data-dir`; the
autouse safety guard rejects port `17082` and blocks socket connections to it.

Results are hardware-, browser-, and load-dependent. This is a small local
sample (six end-to-end subprocess latency observations and 14 workflow operation
events), not a production load test; reruns will vary. The raw DOM includes an intentionally
augmented hidden app-shell payload and should be interpreted as a benchmark
fixture, not a universal page-size claim. The isolated profile and explicit
port guard preserve the zero-live-profile-pollution invariant.
