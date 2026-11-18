# AGENTS.md

> **A rewrite is in progress (from 2026-11-19).** InsightCrew is being rebuilt from an empty directory as a multi-agent trading assistant. Start at [docs/HANDOFF.md](docs/HANDOFF.md), which says where the work stands; [docs/BLUEPRINT.md](docs/BLUEPRINT.md) says what to build.
>
> Until this file is rewritten in work package 11, everything below under "What this is", "Commands", "Invariants", "Things that will surprise you" and "Checking your work" describes version 1, the data analyst, whose code work package 0 deletes. For the rewrite, the rules are BLUEPRINT.md section 0.2, the conventions in its section 15 and the invariants in its section 16. "Working rules" at the end still applies.
>
> **The rewrite does not keep version 1's "standard library only" rule.** The owner withdrew it on 2026-11-19. Version 2 runs on Python 3.12 with maintained libraries (Pydantic AI, Pydantic, FastAPI, Typer), managed by uv: the packages are those in `pyproject.toml` at the versions in `uv.lock`, and adding or upgrading one needs the owner's agreement. Every command goes through `uv run`.

Working rules for AI coding agents in this repository. Human-facing explanation lives elsewhere: [README.md](README.md) is the landing page, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the design, [docs/EVALUATION.md](docs/EVALUATION.md) holds results and failure cases, and [docs/ROADMAP.md](docs/ROADMAP.md) tracks known issues. Link to those rather than repeating them here.

## What this is

InsightCrew is a course project (COMP5584): a multi-agent system that answers metric questions over two CSV tables and audits each number before reporting it. Five roles: Profiler, Planner and Analyst call an LLM; Critic and Reporter are deterministic code. The Critic's independence from the LLM is the point of the project.

- Python 3.9 or later, standard library only. No packages to install.
- Run every command from this directory. Modules are imported as top-level names (`core`, `agents`, `tools`, `config`).
- Code comments, console output, reports and metric names are in Chinese. Documentation is in English.
- With no `IC_API_KEY`, the system runs offline against scripted model replies in `core/scripts.py`. Everything except the model replies is real.

## Commands

```bash
python3 ask.py "2026年第三季度华东区的营收是多少？" --no-open --quiet   # one run; expect 1,036,538.28 after 2 rounds
python3 ask.py "<question>" --compare        # with and without the Critic
python3 -m ui.render_report [runs/<dir>]     # rebuild report.html; defaults to the latest run

python3 -m eval.inject_test                  # Critic sensitivity; expect 40/40 rejected, 0/10 false alarms
python3 -m eval.consistency_test             # metric dictionary vs SQL implementation
python3 -m eval.mock_online_test             # real-model code path against a local fake server
python3 -m eval.run_eval --mode full         # also no_critic, single; offline expect 12/12, 2/12, 2/12

python3 check_api.py                         # real-model connectivity; needs IC_API_KEY
python3 -m eval.online_eval                  # 12 questions on a real model; needs a key and costs money
```

## Invariants

These hold the design together. Do not break one as a side effect of another change. If a task requires changing one, say so explicitly and get agreement first.

1. **The Critic makes no model calls.** `agents/critic.py` decides with a numeric tolerance (L2) and a set comparison against the metric dictionary (L3). An LLM judge for numbers would remove the project's central claim.
2. **The metric definition stays structured.** `filters` has six keys: `region`, `start`, `end`, `exclude_cancelled`, `coupon_deduction`, `subtract_refund`. Do not replace any with free text.
3. **The two computation paths stay independent.** `tools/metrics.py` is for the Critic and the evaluation scripts. The Analyst's prompt may contain a metric's definition and formula, never its `required_filters`, the SQL, or the expected value. The audit hint gives the size of a deviation, not the correct number.
4. **Errors go to the layer that can fix them.** A definition issue (L3) returns to the Planner. A numeric mismatch under a compliant plan (L2 only) returns to the Analyst with the plan unchanged. See `Orchestrator._next_hint`.
5. **The Reporter does not compute.** Every number in a report comes from an artifact on the blackboard.
6. **The sandbox is not weakened.** Keep the static check, the subprocess that does not inherit the parent environment, and the timeout. Keep both `-I` and `-X utf8` on the subprocess command.
7. **A metric is registered in three places**: `data/metric_dict.json`, `tools/metrics.py` (`SUPPORTED` and `_DISPATCH`), and `core/question_parser.py` (`METRIC_ALIASES` and `SUPPORTED_METRICS`). Offline demos also need a branch in `core/scripts.py`. Run `eval.consistency_test` afterwards.
8. **Decline rather than guess.** Zero matching rows, a period outside the data, or an unknown region must produce a refusal, never the number 0. Failures inside a run become a degraded report, not a traceback.
9. **No third-party dependencies**, in the system or in the code the Analyst generates.

## Things that will surprise you

- **`eval.inject_test` rewrites a tracked file.** `eval/result_inject.json` was committed with Windows line endings, so on macOS or Linux every line changes while the content stays the same. After running it, restore the file: `git checkout -- eval/result_inject.json`.
- **`eval.run_eval` creates `eval/result_<mode>_<timestamp>.json`** on every run. Delete the ones you created unless asked to keep them.
- **`eval.online_eval` overwrites `eval/result_online.json`**, the only archived real-model result. Copy it first.
- **Run directories collide within one second.** Runs started in the same second share a `runs/<timestamp>/` directory: traces are appended together and `blackboard.json` is overwritten. When inspecting run output from a script, wait a second between runs.
- **Relative dates use today's date.** "上季度" (last quarter) and questions with no year resolve against `date.today()`.
- **Prompt changes cannot be validated offline.** The scripted model ignores prompts. Changes to a `SYSTEM` prompt in `agents/` need a real-model run. If you cannot do one, say the change is untested.
- **Some comments and launcher scripts state things that are not true**, such as Python 3.8 support. [ROADMAP.md](docs/ROADMAP.md) finding F12 lists them. Check a claim against the code before repeating it in documentation.
- **The message bus is a log.** Nothing reads messages back. Control flow is in the Orchestrator, and data moves through the blackboard.

## Checking your work

Run the smallest relevant check, then widen it.

| Change | Checks |
|---|---|
| Documentation only | Links and anchors resolve. Every number is either re-run or labelled as archived. |
| Any Python file | The first four commands under `eval` above, and the default question. Compare against the expected results listed there. |
| Metric, dictionary or SQL | `eval.consistency_test` and `eval.inject_test`, then the row above. |
| Sandbox | Confirm blocked code is still blocked and the default question still runs, then the "any Python file" row. |
| Agent prompts or `core/llm.py` | `eval.mock_online_test` for the plumbing. Behaviour needs `eval.online_eval` with a real model. |

Report which commands you ran and what they returned. If a check could not be run, say so and say what remains unverified.

## Working rules

- **Known issues are tracked, not fixed in passing.** [docs/ROADMAP.md](docs/ROADMAP.md) lists findings F1 to F17 with evidence. Fix one only when asked. When one is fixed, update the roadmap and, where it appears, [docs/EVALUATION.md](docs/EVALUATION.md) and the limitations in the README.
- **Keep results honest.** Distinguish what was re-run from what is archived, and offline results from real-model results. The offline ablation is a mechanism demonstration: its first answer is wrong by design.
- **Keep the README a landing page.** Detail belongs in `docs/`.
- **Leave run output out of git.** `runs/` and `.env` are ignored. Never commit an API key.
- **Do not commit or push unless asked.**
- The sibling directories `TradingAgents-main/` and `open-multi-agent-main/` in the workspace are reference material for comparison. They are not part of this repository and should not be edited.
