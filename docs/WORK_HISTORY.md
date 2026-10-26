# OmniBrowser — work history and delivery review

Status: current v2 scope accepted for use; historical review prepared 2026-10-21, through published implementation commit `ff966eb`.

- Current product/repository: [OmniBrowser-v2](https://github.com/huutrungle2001/OmniBrowser-v2), public; local canonical project `OnGoing/OmniBrowser`.
- Predecessor: [OmniBrowser](https://github.com/huutrungle2001/OmniBrowser), private, retained as the legacy snapshot `46eef12`. Private is a visibility setting, not a claim that GitHub's archive flag is enabled.
- Current scope: attach to existing Chrome; seven deterministic CLI/MCP tools; clean paged content/links/controls; frame-aware exact refs; native actions, screenshot/coordinate fallback, scrolling and cookie-scoped HTTP downloads. No internal LLM, recipe/workflow/broker service or compatibility layer.
- The current repository's first commit publishes already-written work. It does not mean the implementation began on publication day.

## How to read the timing

- Dates/times use **Europe/London** (BST, UTC+01:00 for this history). Source logs/GitHub often use UTC; repository creation dates are not development start dates.
- `agent_turn` is the recorded `task_complete.duration_ms / 1000` for one agent turn; it includes tool/browser waits. `agent_turn_sum` adds only the explicitly listed turns, not the gaps between them. Display durations round to a second; the numeric seconds retain millisecond precision.
- These are **agent elapsed times, not measured human working hours, CPU time, billable token totals, or the complete cost of the project**. Separate agents can overlap. Do not treat their sum as a single wall-clock project duration.
- `commit_window` is a historical lead-time proxy between the named Git commits, including review, waiting and overnight gaps. It is not measured active work. `unknown` means there is no defensible duration; keep a database duration null, not zero.
- Git history was inspected on the real `main` branches, excluding local agent checkpoint refs. There were no GitHub issues/PR stories returned for the current repositories; the story IDs below reconstruct substantive work from commits and session records, not an invented issue tracker.
- A completed engineering/experiment-review task is not a claim that every external model request succeeded or every crawler completed. Known limitations and unsuccessful trials remain visible.

## Recorded delivery effort

Seven identified v2 implementation/correction turns total **6,652.656 seconds = approximately 1h 50m 53s**. This excludes the legacy implementation, planning, reviews, installation/cleanup, experimental crawl runtime, user deliberation, and the 52.397-second historical blocked-gate response. Supporting work is itemized below rather than silently folded into a misleading total.

The v2 rewrite audit began on 2026-10-19; implementation and corrections ran on 2026-10-19–21. The broader predecessor history begins at 2026-09-17 00:53:06 London time (2026-09-16 23:53:06 UTC).

## 1. Predecessor history: what was built, then deliberately retired

The private predecessor contains **125 main-history commits** through `46eef12`. The following are milestone windows reconstructed from commit timestamps. Every duration in this section has metric kind **commit_window**, not active work.

| Story ID | London timestamp bounds | Historical task | Lead-time window | Seconds | Measurement boundary / GitHub evidence |
|---|---|---|---:|---:|---|
| OB-L01 | 2026-09-17 00:53:06 → 2026-09-17 02:00:52 | Core semantic scanner, refs and initial multi-agent setup | 1h 7m 46s | 4066 | specification/bootstrap → final recorded approval; [72b1dd3](https://github.com/huutrungle2001/OmniBrowser/commit/72b1dd33f64d4cbf0a4c36901c2d3bc64de442d8) → [47fe847](https://github.com/huutrungle2001/OmniBrowser/commit/47fe84798d73e53a771db35015009e22738ef3de) |
| OB-L02 | 2026-09-17 02:01:06 → 2026-09-17 02:35:55 | Action engine and diagnostic primitives | 34m 49s | 2089 | specification/bootstrap → final recorded approval; [141520d](https://github.com/huutrungle2001/OmniBrowser/commit/141520db6e93d09b888c80bf33790615d2385d1e) → [2c08e43](https://github.com/huutrungle2001/OmniBrowser/commit/2c08e43d3b5779c57cdf5504db2a19a59e904a19) |
| OB-L03 | 2026-09-17 02:36:27 → 2026-09-17 02:48:09 | Unified CLI and production packaging | 11m 42s | 702 | specification/bootstrap → final recorded approval; [545959b](https://github.com/huutrungle2001/OmniBrowser/commit/545959bc4d6be290ec0356458eb909c3f510d05c) → [f1dbfc3](https://github.com/huutrungle2001/OmniBrowser/commit/f1dbfc3f8f689359cbfce9213b150e4ab1569710) |
| OB-L04 | 2026-09-17 13:47:47 → 2026-09-17 16:07:28 | Real-world validation and telemetry | 2h 19m 41s | 8381 | specification/bootstrap → final recorded approval; [5470d1f](https://github.com/huutrungle2001/OmniBrowser/commit/5470d1fa4f68aa973dc9f560d591c27a82284fbb) → [66c7985](https://github.com/huutrungle2001/OmniBrowser/commit/66c79853a5ada0108a5d8717a1c4eb5884268a1e) |
| OB-L05 | 2026-09-17 16:21:46 → 2026-09-17 16:32:34 | Procedural memory, adaptive recipes and network capture | 10m 48s | 648 | specification/bootstrap → final recorded approval; [dc14776](https://github.com/huutrungle2001/OmniBrowser/commit/dc14776311deea4377ef1ce41721d97981c8cd6f) → [f9c0217](https://github.com/huutrungle2001/OmniBrowser/commit/f9c021773d7cf4e5688dff86c34c14bd2882ff38) |
| OB-L06 | 2026-09-18 20:37:52 → 2026-09-18 21:36:38 | Automatic recipe-learning loop | 58m 46s | 3526 | specification/bootstrap → final recorded approval; [b793b77](https://github.com/huutrungle2001/OmniBrowser/commit/b793b774b4488459d43823268ed83d44eafaf2dc) → [3b7e1d5](https://github.com/huutrungle2001/OmniBrowser/commit/3b7e1d5d98f9460b93c6f3de30d15b1ac3d79d02) |
| OB-L07 | 2026-09-19 10:40:49 → 2026-09-19 12:44:56 | Browser concurrency broker and repeated hardening | 2h 4m 7s | 7447 | first implementation → final hardening/approval; [d48557f](https://github.com/huutrungle2001/OmniBrowser/commit/d48557f46a66a3208dcca1dccdd989acb720458b) → [23e687e](https://github.com/huutrungle2001/OmniBrowser/commit/23e687e474b4437054825e4a531c9907ef4e1fb3) |
| OB-L08 | 2026-09-19 13:02:20 → 2026-09-19 14:37:48 | Implicit flight recorder and domain-scoped procedural cache | 1h 35m 28s | 5728 | first implementation → final hardening/approval; [092f996](https://github.com/huutrungle2001/OmniBrowser/commit/092f996c5aec617ecef1565abd1f6671def22182) → [1827dec](https://github.com/huutrungle2001/OmniBrowser/commit/1827deca460bc4ece05601ca678a14f6307914d9) |
| OB-L09 | 2026-09-19 14:39:19 → 2026-09-19 15:42:10 | Guarded state transitions and risk classes | 1h 2m 51s | 3771 | specification/bootstrap → final recorded approval; [a848bcb](https://github.com/huutrungle2001/OmniBrowser/commit/a848bcb888fb866217dd98428d403c959ec318ef) → [96c6ea7](https://github.com/huutrungle2001/OmniBrowser/commit/96c6ea71fe482be6eabfe5e243e13e259017749a) |
| OB-L10 | 2026-09-19 15:42:42 → 2026-09-19 16:47:22 | State-transition graph and workflow composition | 1h 4m 40s | 3880 | specification/bootstrap → final recorded approval; [278258b](https://github.com/huutrungle2001/OmniBrowser/commit/278258b3950199bddb593cb477c703e3b60c3718) → [a24b65c](https://github.com/huutrungle2001/OmniBrowser/commit/a24b65cc76a1f8a4a49823dd53158c6b66678f57) |
| OB-L11 | 2026-09-19 16:48:10 → 2026-09-19 18:03:31 | Cross-agent promotion, quarantine and shared-cache governance | 1h 15m 21s | 4521 | specification/bootstrap → final recorded approval; [9dc47bf](https://github.com/huutrungle2001/OmniBrowser/commit/9dc47bf9effb4d16376ef08e1e6e06d125550749) → [79e07fe](https://github.com/huutrungle2001/OmniBrowser/commit/79e07febda5b5b4ef26187dbf7f032e8bc17cd4e) |
| OB-L12 | 2026-09-19 18:04:03 → 2026-09-20 13:55:51 | Self-healing anchors and alternate transitions | 19h 51m 48s | 71508 | specification/bootstrap → final recorded approval; [7ce9373](https://github.com/huutrungle2001/OmniBrowser/commit/7ce937397182e960743c602fd8c56242c6aa6669) → [58551b3](https://github.com/huutrungle2001/OmniBrowser/commit/58551b39689781bd9908b741c7fff0ef083c4068) |
| OB-LMCP | 2026-10-05 22:39:55 → 2026-10-06 03:02:09 | MCP foundation plus eight implementation/review attempts | 4h 22m 14s | 15734 | specification/bootstrap → final recorded approval; [e16ea39](https://github.com/huutrungle2001/OmniBrowser/commit/e16ea3956f7f75cb1a758b37f9991f043289a901) → [cb36fc7](https://github.com/huutrungle2001/OmniBrowser/commit/cb36fc7ed53de0a5dceef97e2d5ce5a00cd5708d) |

Important limits: OB-L07/OB-L08 begin at the first implementation commit, so their windows omit earlier work. OB-L12 includes an overnight gap; **19h 51m 48s is not 20 hours of coding**. The MCP milestone ran from 2026-10-05 22:39:55 to 2026-10-06 03:02:09 London time and needed eight recorded attempts before approval; the Git window is 4h 22m 14s, not a profiler measurement.

Other material predecessor tasks, with **unknown active durations**:

| Story ID | London date | Task / evidence | Duration |
|---|---|---|---|
| OB-LEGACY-PACKAGE | 2026-09-17 | Packaged SKILL/pyproject/README and added a curl installer: `59a6475`, `f1ff682`. | unknown |
| OB-LEGACY-CHAT | 2026-09-19 | Added ChatGPT upload recipes, conversation sessions/tab isolation and generic session resolution: `6196152`, `0e2b66d`, `fb75efa`. The first commit is 00:20 London time on Sep 19, not Sep 18. | unknown |
| OB-LEGACY-FOCUS | 2026-09-20 | Removed connectOverCDP color-scheme override, addressed flicker/high CPU and added a raw-CDP screenshot fast path: `c33d4be`. | unknown |
| OB-LEGACY-PYTHON | 2026-09-23 | Added dataclass-slots fallback for Python below 3.10: `b3f5149`. | unknown |
| OB-LEGACY-POWERBI | 2026-09-30 | Added Power BI Playground dashboard recipes: `9033417` (Sep 29 UTC, Sep 30 London). | unknown |
| OB-LEGACY-CONTROLLER | 2026-10-04 | Updated CDP controller/recipes and captured a local checkpoint: `2e38526`. | unknown |
| OB-LEGACY-PORT | 2026-10-07 | Standardized CDP port 17082 and removed 9222 fallback from guidance: `9112e65`. | unknown |
| OB-LEGACY-RETIRE | 2026-10-20 | Preserved final legacy snapshot `46eef12`, made repository private and replaced local product with v2. Timing is part of OB-V2-012, not another independent cost. | shared with OB-V2-012 |

## 2. Rewrite, rollout, real usage and correction timeline

Delivery-turn rows OB-V2-004/005/007/011/018/020/024 form the 6,652.656-second total. All other measured rows are separately recorded support/review/operations turns. A row containing several turns reports their sum, not the entire date/time span.

| Story ID | Date/time (Europe/London) | Task and outcome | Recorded elapsed | Seconds | Basis and source turn IDs |
|---|---|---|---:|---:|---|
| OB-V2-001 | 2026-10-19 (2 separate turns) | Audit the legacy engine and actual Moodle tool use. Found roughly 9,142 runtime lines and 6,754 test lines; the observed crawl used no workflow/recipe-run/broker. Adopted a deterministic small-tool replacement. | 11m 18s | 678.044 | agent_turn_sum; R:01a15201-c504-7373-b4b6-a2fde218b7a3, 01a15456-d8a5-7a51-a8f8-33c5ecb14dda |
| OB-V2-002 | 2026-10-19 14:43:29 → 2026-10-19 14:50:58 | Define a clean Omni CLI project and module contract. Wrote GOAL/PLAN for list_tabs, goto, observe, act, download; root omni.py and src/ modules; no embedded agent loop. | 7m 29s | 449.085 | agent_turn; R:01a15467-2b43-7920-b2ad-8570f518d908 |
| OB-V2-003 | 2026-10-19 14:53:10 → 2026-10-19 15:00:22 | Prepare implementation handoff and agent environment. Prepared Workbench/taskboard handoff and a prefilled Sol 6.1 high goal. | 7m 12s | 431.821 | agent_turn; R:01a15470-07f5-7442-9eb9-f690ebbe7dd6 |
| OB-V2-004 | 2026-10-19 15:27:20 → 2026-10-19 15:43:20 | Implement the initial five-command prototype. Built attach-only CLI, scanner, exact refs, and authenticated downloads; local checks passed. Live-account verification was explicitly deferred, not counted as complete. | 16m 0s | 959.546 | agent_turn; I:01a1548f-4ff4-73f1-aa0b-c738420feeec |
| OB-V2-005 | 2026-10-19 15:43:20 → 2026-10-19 15:48:12 | Correct three prototype contract gaps. Fixed HTTP-page ref generation, HTML-masquerading-as-file rejection and POST-only rejection; 58 local checks passed in this historical phase. | 4m 52s | 291.670 | agent_turn; I:01a1549d-f44b-7243-9817-9bf41aa13f62 |
| OB-V2-006 | 2026-10-19 15:45:06 → 2026-10-19 15:48:54 | Study browser-use and browser-harness. Cloned/read upstream architecture; chose selective MIT source vendoring rather than importing its whole agent/session framework. | 3m 48s | 228.272 | agent_turn; R:01a1549f-925a-7053-ab5e-859efa7e259e |
| OB-V2-007 | 2026-10-19 17:05:12 → 2026-10-19 17:17:16 | Vendor the reader; add persistent STDIO MCP and a skill. Joined DOM, accessibility and snapshot data by backendNodeId; exposed five deterministic tools; installed editable dependencies, skill and Codex registration. | 12m 3s | 723.146 | agent_turn; I:01a154e8-e9c2-7421-98e3-9de333b2cd4f |
| OB-V2-008 | 2026-10-19 17:17:31 → 2026-10-19 17:21:08 | Review the first MCP/CLI installation. Confirmed canonical entries, five-tool discovery and existing-Chrome CDP target; later Moodle runs supplied live verification. | 3m 37s | 216.859 | agent_turn; R:01a154f4-2f45-70e3-8276-0c4053843168 |
| OB-V2-009 | 2026-10-19 (5 separate turns) | Launch Moodle crawl trials across tools and models. Created isolated course workspaces and prompts; started Omni/Gemini, Omni/Luna, Browser Use/Gemini and Browser Use/Luna. Elapsed is setup only, not crawler runtime. | 23m 37s | 1417.321 | agent_turn_sum; R:01a154ff-f011-7f51-8ce1-92c296c847da, 01a15509-6e39-72d2-84f8-45df3040f6dd, 01a1551c-8782-7dd3-a180-11cf2fa4fe74, 01a15529-c48b-7740-9058-8b15c982fe0a, 01a1561f-dda8-73e0-bf13-6b3bf9b20e83 |
| OB-V2-010 | 2026-10-19 (3 separate turns) | Inspect crawl completeness and rerun Luna with a stricter prompt. Compared same-course files/manifests and hashes, not different course sizes. Captured incomplete Luna runs; comparison results are detailed below. | 14m 34s | 874.279 | agent_turn_sum; R:01a1562a-e264-70e3-a1b5-674246e18bce, 01a1563f-8c3d-7f60-b269-6f75c82bf7ea, 01a15655-478e-72f3-9bd7-79ce975b73de |
| OB-V2-011 | 2026-10-19 23:42:13 → 2026-10-20 00:03:47 | Extend to seven commands and modular command files. Added paged observe, screenshots as MCP ImageContent, native press/select and scroll; one command per file and a persistent single-thread MCP owner. | 21m 34s | 1294.275 | agent_turn; I:01a15654-62ae-7f43-95f7-ea7a52aaa9e3 |
| OB-V2-012 | 2026-10-20 00:32:33 → 2026-10-20 00:42:42 | Replace the local legacy project and publish OmniBrowser v2. Preserved/pushed the legacy snapshot privately, published v2 publicly, moved the new implementation into OmniBrowser, removed the separate Omni workspace and updated paths. | 10m 9s | 608.668 | agent_turn; R:01a15682-7a13-71f0-8f23-f34e1ce0722f |
| OB-V2-013 | 2026-10-20 (3 separate turns) | Remove development clutter and consolidate skill installation. Removed caches/root handoff clutter, stopped crawl agents, moved trial folders to Trash, placed fixtures under tests, and consolidated the canonical omnibrowser skill. | 8m 21s | 501.105 | agent_turn_sum; R:01a1568d-4525-7ab0-8c77-6c88debcbcf7, 01a15690-faaf-7b12-910d-455ea9c8d9fb, 01a1569a-8611-7331-b983-0ca6d4b36bf0 |
| OB-V2-014 | 2026-10-20 01:14:40 → 2026-10-20 01:16:17 | Shorten the agent-facing skill. Reduced 66 lines/about 3.6 KB to 42 lines/about 1.8 KB while preserving seven tools and safe stale-ref/cursor recovery. | 1m 37s | 97.181 | agent_turn; R:01a156a9-06d7-7e93-b84b-437e1a9c4d5d |
| OB-V2-015 | 2026-10-20 13:48:46 → 2026-10-20 13:57:21 | Diagnose a disconnected MCP client. Found Transport closed in the affected client rather than a Chrome connection failure; documented process recreation/reconnect semantics. | 8m 36s | 515.775 | agent_turn; R:01a1595b-6bc2-7723-bcae-24a2cffd9c05 |
| OB-V2-016 | 2026-10-21 (2 separate turns) | Investigate unwanted Python Dock applications. Traced them to separately enabled browser-use MCP processes, not Omni's vendored DOM code; adjusted host launch configuration and traced subsequent process origins. | 9m 48s | 587.732 | agent_turn_sum; R:01a15bb5-6fe7-7012-9e29-a0c48982b383, 01a15bff-095c-7500-91b7-a8e629c7a9d8 |
| OB-V2-017 | 2026-10-21 (3 separate turns) | Specify frame-aware browsing and canvas coordinate fallback. Designed browser/ and reading/ modules, recursive frames/OOPIF and click_at screenshot mapping; explicitly removed backward-compatibility wrappers from scope. | 7m 26s | 446.437 | agent_turn_sum; R:01a15c39-d859-77d1-bbef-abd6d77d623b, 01a15c77-0f3b-7ec1-a7cb-c22624490a1e, 01a15c7b-82c9-7ac3-a1ea-777c9db9a27c |
| OB-V2-018 | 2026-10-21 04:25:25 → 2026-10-21 04:51:10 | Implement frames/OOPIF, exact frame refs and coordinate input. Live CLI/MCP verified nested/same-/cross-origin frames, actual OOPIFs, shared refs, coordinate clicks, screenshot mapping, pagination and packaging. | 25m 45s | 1545.027 | agent_turn; F:01a15c7e-0648-7943-85e8-01f5dcc54735 |
| OB-V2-019 | 2026-10-21 05:27:18 → 2026-10-21 05:33:52 | Review frame implementation on a live browser. Confirmed frame targeting and PNG metadata; identified over-sensitive whole-DOM mutation rejection as a separate follow-up. | 6m 34s | 394.091 | agent_turn; R:01a15cb6-ae25-7e51-b177-d2d399c22c01 |
| OB-V2-020 | 2026-10-21 18:08:08 → 2026-10-21 18:14:07 | Stop rejecting observe for unrelated DOM churn. Removed revision-equality gating while retaining document/URL/baseURI and frame checks; genuine stale refs/cursors still failed safely. | 5m 58s | 358.400 | agent_turn; F:01a15f6f-3f07-7763-aad5-730bfdb36496 |
| OB-V2-021 | 2026-10-21 (4 separate turns) | Diagnose and reproduce false STALE_REF on reactive forms. Inspected hackathon agent calls, then reproduced search/profile/apply failures. The exact node/backend ID survived transient name-attribute churn; no form was submitted. | 15m 21s | 921.485 | agent_turn_sum; R:01a15fdd-3da9-75a3-adc7-9e97624edd61, 01a15fe6-acfc-74a2-844c-b24a8c56089f, 01a15ff3-a1cc-7313-a2c8-3147b7de0b54, 01a16004-6a1d-7573-b6c2-a276206c9a5a |
| OB-V2-022 | 2026-10-21 21:13:31 → 2026-10-21 21:16:59 | Obtain independent ChatGPT and Gemini opinions. Both supported the exact-node mapping and identified overly eager invalidation, rather than a locator problem. This is review time, not another implementation. | 3m 28s | 208.256 | agent_turn; R:01a16018-f7e9-7382-a6c1-46ae99dddedf |
| OB-V2-023 | 2026-10-21 (2 separate turns) | Write the narrow ref-lifecycle task and submit it correctly. Created REF_STABILITY_PLAN.md, preserving cursor semantics; resolved a pasted-but-unsent tmux prompt before starting the goal. | 7m 10s | 430.108 | agent_turn_sum; R:01a1602b-3369-7fd0-9dc3-3edbb82a1d74, 01a1603b-7790-71d3-9376-9c719ec929e2 |
| OB-V2-024 | 2026-10-21 21:37:38 → 2026-10-21 22:02:18 | Fix transient-attribute ref invalidation. Removed unconditional attribute-event invalidation; retained exact-node/removal/identity checks and added safe reuse guards. Original refs worked for sequential Apply fields and Search. | 24m 41s | 1480.592 | agent_turn; F:01a1602f-0a6e-7573-a24a-f7d6ef989f20 |
| OB-V2-025 | 2026-10-21 23:31:25 → 2026-10-21 23:32:14 | Review the final ref fix and rollout requirement. Code and recorded live checks accepted. Existing page registries retain old JavaScript, so those pages need reload/new tabs; MCP reconnect alone is insufficient. | 0m 49s | 48.506 | agent_turn; R:01a16097-3848-7442-9aec-f34067d4b48f |
| SHARED-PUBLISH-20261021 | 2026-10-21 23:38:12 → 2026-10-21 23:39:12 | Commit and push both repositories. Published ff966eb (OmniBrowser v2 public) and ae8dec3 (Oracle private); both main branches were clean and synchronized. This is one shared 60-second turn, not two independent costs. | 1m 0s | 60.102 | agent_turn; R:01a1609d-6c50-7993-90f7-95a1d4be5da6 |

## 3. GitHub publication milestones

| London timestamp | Commit | Published result |
|---|---|---|
| 2026-10-20 00:40:54 | [a2928d3](https://github.com/huutrungle2001/OmniBrowser-v2/commit/a2928d3) | Published v2 after separate local implementation and use. |
| 2026-10-20 00:41:07 | [5336ce7](https://github.com/huutrungle2001/OmniBrowser-v2/commit/5336ce7) | Omitted generated lock/local material and normalized vendor whitespace. |
| 2026-10-20 01:03:24 | [e4eb2ac](https://github.com/huutrungle2001/OmniBrowser-v2/commit/e4eb2ac) | Consolidated skill and moved fixtures under tests. |
| 2026-10-20 01:15:47 | [ff1ad64](https://github.com/huutrungle2001/OmniBrowser-v2/commit/ff1ad64) | Streamlined the agent skill. |
| 2026-10-21 23:38:56 | [ff966eb](https://github.com/huutrungle2001/OmniBrowser-v2/commit/ff966eb) | Published browser/reading module split, frames/OOPIF, click_at/mapping and both DOM/ref corrections together. |

GitHub reports repository creation at 2026-10-20 00:41:08 London. The final implementation commit contains multiple earlier tasks, so its timestamp must not replace their task completion times. Counts here exclude the present history document.

## 4. Moodle experiment outcomes and efficiency

These are historical experiment-review findings from R:01a15655-478e-72f3-9bd7-79ce975b73de, not newly rerun crawls. The oddly named Applied_AI_Moodle folder was used in the Applied Stats comparison; compare actual course content, not folder names or the unrelated WM9Q1 course.

| Trial | Outcome at final recorded review | Recorded runtime | Token/cost limitation |
|---|---|---|---|
| Omni + Gemini | Complete: 16 unique Moodle attachments and 10 Markdown files; additionally collected external MATLAB/Excel material. | Final authoritative duration unavailable; an earlier snapshot estimated about 45 minutes. | Earlier runtime token counters exist but are not a normalized final billable total. |
| Browser Use + Gemini | Complete: same 16 unique attachments, 13 Markdown files, 20 external-link metadata entries. Attachment SHA-256 hashes matched Omni-Gemini. | 40m 57s; 133 MCP calls. | No comparable final token/pricing record. |
| Omni + Luna, restarted stricter prompt | Incomplete: four PDFs; several sections/files still pending. | 4m 12s. | Historical runtime reported 3.51M tokens; incomplete result is not an equivalent successful workload. |
| Browser Use + Luna, restarted stricter prompt | Incomplete: 11 attachments; seminar and four assessment files missing. | Approximately 20m 09s. | Historical runtime reported 15.24M tokens; counting/pricing not normalized. |

The first Luna snapshots (316.8s and 228.8s) were different, earlier runs, not alternate durations for the restarted trials. Do not mix them. No defensible overall speed, token-cost or monetary winner can be computed from these heterogeneous records. The supported conclusion is that Omni worked end-to-end for this Moodle workflow and its scoped HTTP downloader reduced download-dialog handling; two complete Gemini results agreed on attachment bytes. Prompt completeness and agent persistence materially affected success.

## 5. What was effective, and what was rework

- The legacy product accumulated broker/learning/workflow/governance machinery beyond the actual task. Its retirement is real rework, not delivered value to count twice in today's v2 feature list.
- Inspecting actual tool calls before rewriting was useful: no workflow, recipe-run or broker was needed by that observed crawl. The five-command core, later seven tools, matched the agent's real workflow.
- Selective upstream MIT reader reuse retained DOM/AX/layout capability without installing browser-use's full agent framework. Correct attribution remains in the vendored source.
- Small command modules and browser/reading boundaries kept the current handwritten logic readable; no new workflow framework was necessary for iframe support.
- Frames/coordinate input delivered in one recorded 25m 45s turn, then required two genuinely useful corrections: whole-page churn gating (5m 58s) and transient attribute ref invalidation (24m 41s). These fixes demonstrate that conservative checks can themselves be false-positive bugs; they do not establish universal web compatibility.
- Cross-tool comparisons were useful only after checking completeness and identical file hashes. Fast incomplete crawls are not better delivery.
- No normalized token invoice or human timesheet exists. Do not turn model-reported counters or Git commit gaps into an efficiency ratio or dollar estimate.

## 6. Closeout and remaining operating limits

The accepted current scope is ready for ordinary use; no blocking implementation task was identified in the final review. This closes the delivery milestone, not all future maintenance.

- Existing MCP processes need recreation/reconnect to load Python changes. Pages already initialized with the old injected ref registry need a reload or a new tab to install the corrected JavaScript. Do not reload an unsaved form just to update the registry.
- Exact refs still reject real navigation, replacement, disconnection and changed identity. They do not guarantee that a web app has not reused one node for a different business object.
- Native press/select, iframe reads and tested screenshot coordinate mapping work. Canvas dragging/typing, inner-pane/iframe scrolling, closed shadow roots and pinch/offset-viewport input are outside the implemented contract.
- HTTP downloads do not promise POST/blob/auth-challenge handling. See README for capture/byte/frame caps and coverage limits.
- Real Apply/Search verification succeeded without Submit/terms acceptance; the final profile/create recheck redirected and therefore remains unverified on that exact surface. Remove/reinsert protection was retained but not re-exercised in the last follow-up.
- No shared Chrome daemon or automatic old-registry version migration was implemented. These are not silently pending requirements for the accepted scope.

## Workbench database import contract

Use the story IDs as stable **source/deduplication keys**, not as replacements for Workbench's own `T-...` task IDs. Store the source document, source turns/commit bounds, metric kind, elapsed seconds, outcome and verification limit in the existing task `notes` field; the current database has no dedicated duration column. Use the existing task CLI. No schema migration, new time-tracking daemon or separate reporting system is required.

- Reconcile existing tasks first; do not create a second copy of an existing project milestone.
- Completed current-scope stories may be recorded as `done`; mark predecessor features as historically delivered and subsequently retired in notes, not as features still shipping.
- Preserve historical dates in notes. The import/create timestamp is not the historical completion date, and a deadline must not be invented.
- Do not sum `commit_window`, delivery turns, supporting review/ops turns and external crawl runtimes into one purported human-effort figure.
- `SHARED-PUBLISH-20261021` is the same cross-project publishing turn in both reports. Import/count it once, or link the second project to the same event.
- Summary totals are derived views, not additional work sessions. This closeout/report-writing task is not included in historical execution totals.

For Workbench reconciliation, existing relevant records at review time were T-034 (project Omni, waiting) and T-009 (OmniBrowser, todo). Inspect each task's actual done_when/notes before updating; do not mark an unrelated blocker done merely because the product milestone is accepted.

## Sources and reproducibility

- GitHub current repository commits: `gh api repos/huutrungle2001/OmniBrowser-v2/commits`; private predecessor: `gh api repos/huutrungle2001/OmniBrowser/commits?per_page=100&page=1` and page 2.
- R = coordinator session `~/.codex/sessions/2026/10/19/rollout-2026-10-19T02-52-41-01a151dc-6703-70d1-92b9-acdd32fa2ede.jsonl`.
- I = initial Omni implementation session `~/.codex/sessions/2026/10/19/rollout-2026-10-19T14-58-09-01a15474-9621-7cb0-b02d-f62933b49ffe.jsonl`.
- F = frame/ref implementation session `~/.codex/sessions/2026/10/21/rollout-2026-10-21T04-22-45-01a15c7b-94c6-74c1-8dd6-ef0525ef7416.jsonl`.
- Timing comes from each matching `event_msg/task_complete` event's `started_at`, `completed_at` and `duration_ms`, not the rollout filename or rounded final prose. Supporting browser verification also appears as successful CommandExecution outputs in those sessions.
- Private predecessor result records are retained under `Documents/Archives/OmniBrowser-v1-20261020/.agents/communication/`. Some trial folders were intentionally moved to Trash after review; this report does not assert they are still present.
- Scope/verification: README, REF_STABILITY_PLAN.md, canonical skill and code at `ff966eb`. No account emails, prompt contents, cookies or credentials are reproduced here.
