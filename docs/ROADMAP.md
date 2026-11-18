# InsightCrew roadmap

Where the project stands, what an examination of the code on 2026-11-18 found, and what to do in which order.

The project's earlier notes give the submission deadline as **2026-11-23 23:59** (one zip on Canvas). The first two sections are ordered around that date. Confirm the date and format against the course brief.

## Status

| Area | State |
|---|---|
| Five-role pipeline with two feedback edges | Working. Offline run reproduced end to end. |
| Deterministic audit (L2 recomputation, L3 dictionary check) | Working. 40 of 40 injected errors rejected, 0 false alarms. |
| Four metrics, region and time filters | Working. |
| Refusal of unanswerable questions | Working for unknown metrics, regions, periods and channel breakdowns. Not for unrecognised intent: see F1. |
| Real-model run | 12 of 12 with `deepseek-chat`, archived. Not re-run during this examination (no API key). |
| Trace, blackboard dump, HTML replay | Working for single runs. Batch runs collide: see F3. |
| Automated tests, CI | None. The `eval/` scripts are run by hand. |
| Documentation | README, architecture, evaluation, roadmap and agent instructions written 2026-11-18. |

## Before submission

Ordered by how much each item protects the grade for the effort. Rubric weights: system design 30%, implementation 25%, problem formulation 15%, analysis 15%, presentation 15%.

### Do first

| # | Item | Why it matters | Effort |
|---|---|---|---|
| 1 | Run the real-model ablation: `eval.run_eval` in `full`, `no_critic` and `single` modes with an API key, and archive the three result files | The only ablation on file is offline, where the first answer is wrong by design. A real-model comparison is the strongest evidence that the multi-agent design contributes, and it costs a few minutes and a few cents. `run_eval` writes new timestamped files and leaves the archived online result alone. | 30 min |
| 2 | Fix F2: the first model call can crash the run | A network failure at the start of a live demo shows a traceback instead of a degraded report. | 10 min |
| 3 | Fix F1, deterministic part: decline questions with unrecognised intent | The system's main claim is "declines rather than answers wrongly". Six ordinary questions currently break it with high confidence. A grader who tries their own question may hit one. | 1 to 2 h |
| 4 | Fix F4: do not headline a rejected number | Cheap, and removes the one case where the report contradicts the Critic. | 30 min |
| 5 | Decide how to present F14 | Two of five roles make no model calls. Say so up front and explain why, before a grader notices. | Slides only |

### Do if time allows

| # | Item | Effort |
|---|---|---|
| 6 | F3: unique run directories, so evaluation traces are usable as evidence | 10 min |
| 7 | F5, F6, F7, F10: token budget, caveats in the blackboard dump, trace recipient, temp directory cleanup | 1 h total |
| 8 | F15: a single test command, so "it works" is one green line | 1 to 2 h |
| 9 | F12: correct the stale statements in comments and launchers | 15 min |
| 10 | F17: widen error injection to all four metrics | 1 h |

### Submission checklist

- [ ] Demo video and slides (not covered by this repository)
- [ ] Individual contribution statement
- [ ] Zip excludes `runs/`, `__pycache__/`, `.env` and `.git/`; keeps `eval/result_*.json`
- [ ] Zip built with UTF-8 file names. The code has no Chinese file names, but check that any added documents open correctly on Windows.
- [ ] A fresh unzip runs with `python3 ask.py` on a machine with no API key
- [ ] If the audience does not read Chinese: see F16

## Findings

Each finding was verified on 2026-11-18 unless marked otherwise. Line numbers refer to the code on that date. None has been fixed: source code was left unchanged during the examination.

### Correctness and trust

**F1. Nothing checks that the plan matches the question.** High impact.
The scope gate (`core/question_parser.py`, `scope()`) passes any question containing a data-related word, and the Critic compares the value with the plan and the plan with the dictionary. A misread question is answered with confidence "high". Verified offline with six questions: an English region name dropped, "first half" read as full year, "which is higher" answered with a sum, "growth" answered with the base figure, "per day" answered with the total, and "cancelled orders" answered with non-cancelled orders. Details in [EVALUATION.md](EVALUATION.md#1-the-wrong-question-answered-with-high-confidence). Not tested with a real model.
Note that `DOMAIN_WORDS` lists 同比, 环比 and 占比 (year-on-year, period-on-period, share) as signals that a question is in scope, though none is supported.
*Fix, in two steps.* (a) Deterministic: decline when the question contains a modifier the system does not implement (growth, comparison, ranking, share, average per period, half-year, status-specific counts) or Latin-script region names. This matches the existing policy for channels. (b) Optional: add an LLM judge that sees only the question and the structured filters, and can only veto. Numeric verdicts stay deterministic. This is the one check with no deterministic form, and it is where the consensus pattern from the reference systems fits.

**F2. A failed first model call crashes the run.** High impact for a live demo.
`core/orchestrator.py:70` calls `self.profiler.run()` before the loop, outside the `try` at line 90. With an unreachable endpoint, `run()` raises `RuntimeError` and writes no report.
*Fix.* Wrap the call and route the exception to `_degrade`, as the loop already does.

**F3. Run directories collide.** Medium impact.
`core/orchestrator.py:34` names the run directory with one-second resolution. After `eval.run_eval`, one directory held events from 24 runs and a single overwritten `blackboard.json`. `ask.py --compare` in offline mode shares one directory between both runs.
*Fix.* Add milliseconds or a short random suffix. Keep the name sortable, since `ui/render_report.py` picks the latest by name.

**F4. A degraded report headlines a rejected value.** Medium impact.
`agents/reporter.py:63` prints the most recent artifact value as the conclusion whenever one exists. With `IC_MAX_TURNS=1`, the report reads `营收 = 1,189,734.57 CNY`, which the Critic had just rejected.
*Fix.* When the last verdict is REVISE, print "no verified conclusion" and list the rejected value below as an unverified candidate.

**F5. The token budget is configured but never enforced.** Low impact.
`config.py:67` reads `IC_MAX_TOKENS` (default 200,000). No other code refers to it.
*Fix.* Check `self.llm.tokens` against it at the top of the loop, or remove the setting.

**F6. Coverage caveats are missing from** `blackboard.json`**.** Low impact.
`Blackboard.to_dict()` (`core/blackboard.py:97`) omits the `caveats` field. Caveats appear in the report text but not in the structured dump.
*Fix.* Add the field.

**F7. The trace names the wrong recipient for implementation-only rejections.** Low impact.
`agents/critic.py:102` always logs REVISE as Critic → Planner. When only L2 failed, the Orchestrator sends the work to the Analyst, so the trace and the replay page show a recipient that did not act. Found by reading the code; the archived online run has two such cases.
*Fix.* Let the Orchestrator, which makes the routing decision, log the recipient.

**F8. The time limit is checked only between rounds.** Low impact.
A single model call can take up to three attempts of 120 s each plus 11 s of backoff, which is longer than the default `IC_MAX_SECONDS` of 240.

### Sandbox

**F9. The static check is a deny-list and can be bypassed.** Medium impact.
`core/sandbox.py` blocks listed imports and call names. These pass: `open()` on an absolute path, `open()` for writing, `getattr(__builtins__, ...)` to reach `__import__`, and walking `__subclasses__()`. Generated code can therefore read any file the user can, including `.env`. Only environment variables are protected.
*Fix.* Replace the deny-list with an allow-list of imports, reject dunder attribute access and `getattr`, and restrict `open()` to literal relative file names in read mode. Test against a real model afterwards, because a stricter check may reject code that is currently accepted. Real isolation needs a container.

**F10. Temporary directories are never removed.** Low impact.
`core/sandbox.py:95` creates a directory per execution with copies of both CSV files and never deletes it. The test runs for this examination left about 70 in the system temp folder (since removed), and 8 from earlier runs were still there. `eval/inject_test.py` leaves another 50 per run in the same way.
*Fix.* Remove the directory in a `finally` block.

### Reproducibility

**F11. Relative dates depend on the day the code runs.** Medium impact.
`core/question_parser.py:182` uses `date.today()`. "Last quarter" means Q3 only while today is in Q4 2026, and a question with no year defaults to the current year. The archived evaluation includes a "last quarter" question that will resolve differently from January 2027.
*Fix.* Read a reference date from an `IC_TODAY` setting and pin it in the evaluation scripts.

**F12. Stale statements in code and launchers.** Low impact.

- `start.bat` asks for Python 3.8+ and tries `py -3.8`. `requirements.txt` says 3.10+. The code needs 3.9+ (`str.removesuffix` in `core/orchestrator.py:192`).
- The `eval/run_eval.py` docstring says offline mode works only for the first question. It works for all 12.
- The `core/orchestrator.py` docstring describes the file as 150 lines and says avoiding frameworks is a grading point. The file is 287 lines, and the brief does not mention frameworks.
- `agents/profiler.py` says an ablation shows more definition errors without the Profiler. No such experiment exists.
- `agents/planner.py:46` tells the model that seven regions are valid. The data has three. The guard rejects the other four afterwards, but the prompt invites the mistake. Use `available_regions()`.

**F13. Evaluation scripts change tracked files.** Low impact.
`eval.inject_test` rewrites `eval/result_inject.json` with different line endings: a 1,160-line diff with identical content. `eval.run_eval` adds a timestamped file on every run. `eval.inject_test` exits with status 0 even when a case fails.
*Fix.* Add a `.gitattributes` to normalise line endings, write per-run results to an ignored directory, and return a non-zero status on failure.

### Design and presentation

**F14. Two of the five roles make no model calls.**
The Critic and the Reporter are deterministic code. This is the design's main idea, and it is well argued in the architecture document. But the brief asks for "LLM-based agents", and a reader counting model-backed agents will find three.
*Options.* Present it directly as the thesis: not every role should be an LLM, and verification is the role that should not be. Optionally let the Reporter use a model to write a short narrative around numbers it is not allowed to change, with a check that every number in the text appears on the blackboard.

**F15. No single test command and no CI.**
The `eval/` scripts act as tests but are run one at a time and judged by reading their output.
*Fix.* A `tests/` directory using the standard-library `unittest`, covering the sandbox check, the question parser, the Critic's verdict table, and one offline end-to-end run per metric. Then a GitHub Actions workflow for Python 3.9 to 3.13. This keeps the no-dependency rule.

**F16. All output is in Chinese.**
Console, report and replay page. For an English-language demo, narrate or subtitle the output, or add a language setting for the fixed strings.

**F17. Evaluation gaps.** See [EVALUATION.md](EVALUATION.md#threats-to-validity): no real-model ablation, injection covers one metric and six distinct filter sets, five of 12 questions repeat one computation, one run per question, and the Profiler's contribution is not isolated.

## After submission

Ideas that extend the design. Several come from the two reference systems in the workspace.

| Idea | Source | Value |
|---|---|---|
| Different models per role: a cheaper model for the Profiler, a stronger one for the Planner | TradingAgents uses separate quick and deep model tiers | Lower cost, and a new ablation dimension |
| Cross-run memory: append each rejection to a lessons file and give the Planner the relevant ones | TradingAgents keeps a decision log with reflections | Fewer first-round rejections over time, measurable as rounds per question |
| LLM judge for question-to-plan alignment, veto only | OMA's consensus judges | Closes F1 beyond what keyword rules can |
| Per-step execution: run and check each plan step separately | The plan already has steps | Localises errors to a step |
| More dimensions and metrics: channel, growth rates, comparisons, medians | Limitations 1 and 2 in the architecture document | Wider coverage. Each needs a second implementation and a dictionary entry. |
| Charts in the report | Original design proposal | Presentation |
| Container-based sandbox | F9 | A real security boundary |
| Packaging: `pyproject.toml`, an `insightcrew` package instead of top-level `core`, `tools`, `agents`, and a licence | Standard practice | Installable, no `sys.path` edits |
| Real data adapter with schema validation | The CSV column contract in the README | Use beyond the synthetic set |

## Not planned

- **An LLM as the numeric judge.** It would remove the property the design rests on.
- **Adopting an agent framework.** The hand-written loop is the part that is easiest to explain and test.
- **Adding third-party dependencies to the core.** Optional extras are acceptable if the offline demo still runs on a bare Python install.
