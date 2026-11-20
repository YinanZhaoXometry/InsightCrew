# AGENTS.md

Working rules for AI coding agents in this repository. People should start with [README.md](README.md). The design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), results in [docs/EVALUATION.md](docs/EVALUATION.md), and known issues in [docs/ROADMAP.md](docs/ROADMAP.md). Link to those; do not repeat them here.

## What this is

InsightCrew version 2.3: a multi-agent trading assistant with an independent audit, for the COMP5584 group project. Given a ticker and a date, model roles produce a rating and a trade proposal: technical, fundamentals, disclosure, news and sentiment analysts, bull and bear researchers, a research manager, a trader, an optional three-way risk debate, three judges, and a reflector that writes lessons after outcomes settle. The flow is a LangGraph `StateGraph` with parallel analysts and checkpointed resume. Code computes every fact on two paths, audits every cited number and every rule, and refuses with `REVIEW` when it cannot stand behind an answer. Every capability added since 2.1 (channels, portfolio, risk debate, tools, news/macro/sentiment, memory, the approval gate) is off by default: with everything off, a run is byte-for-byte version 2.1. Version 2.3 hardened the flow and the app, added the timing trace (`trace.jsonl`), the `verify` command, run control from the page (cancel, resume, plan preview, exports), the approval gate, and rebuilt the run page from split modules.

- Python 3.12, managed by uv. The packages are those in `pyproject.toml` at the versions in `uv.lock` (version 2.2 added `langgraph` and `langgraph-checkpoint-sqlite` by `uv add`; version 2.3 added nothing). **Do not add, remove or upgrade a package, or change `exclude-newer`, without the owner's agreement.**
- Run every command from this directory, through `uv run`.
- Code comments, docstrings, console output, prompts and reports are in Chinese. Enum values, keys and identifiers are in English. Documentation is in English.
- With no key in `.env`, every run without `--scenario` falls back to scripted replies, and the command says so. With a key, a run without a scenario calls the real model and costs money. Ask the owner before every real-model spend and every network request. When starting `serve` only to look at the page, set `IC_FORCE_OFFLINE=1`.
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md) is the specification (section 19 is version 2.2, section 20 is version 2.3) and [docs/HANDOFF.md](docs/HANDOFF.md) the status board. Read both before a change that touches a name, a rule, a limit or an invariant. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists the twenty files adapted from TradingAgents; each carries its header line. Version 2.3 adapted nothing from either reference codebase.

## Commands

```bash
uv run insightcrew analyze [TICKER] [--date D] [--scenario NAME] [--universe U] \
    [--channels fundamentals,disclosures,news,sentiment,macro] [--portfolio PATH] \
    [--risk-rounds N] [--tools] [--memory] [--approve] [--no-judges] [--debate-rounds N] \
    [--serial] [--quiet] [--no-open]
uv run insightcrew resume RUN_DIR                # continue an interrupted run from its checkpoint
uv run insightcrew verify RUN_DIR                # offline structural check of a journal; no model
uv run insightcrew settle [--known-by D]         # score pending decisions; reflector writes lessons
uv run insightcrew backtest TICKER... --from D --to D [--every N] [--run-id ID] [--yes]
uv run insightcrew serve [--port N] [--no-open]  # local web app on 127.0.0.1 (form, live stream,
                                                 # cancel/resume/approval, plan preview, exports)
uv run insightcrew facts TICKER [--date D] [--universe U]   # ledger + what each channel would read
uv run insightcrew replay RUN_DIR                # rerun from the journal with no model; expect 一致
uv run insightcrew scenarios [NAME]              # expect 36 lines ✓ … 相符
uv run insightcrew check                         # settings without the key; probes each tier (needs a key)
uv run insightcrew fetch TICKER... [--years N] [--news] [--social] [--macro] [--markets]
                                                 # real bars + evidence; the four kinds are off by
                                                 # default and each live request is asked for first
uv run insightcrew eval all                      # inject 19/19+6/6+6/6 (0 false alarms), consistency
                                                 # 277×29, 46×12, tools 63, macro 96, pit 864+16+9+9+91+6+5+63
uv run insightcrew eval ablation                 # 16 real-model runs (v2.0); needs a key; about 0.5 USD
uv run insightcrew eval channels                 # 12 real-model runs (v2.1); about 0.5 USD
uv run insightcrew eval v22                      # the 9 v2.2 cells on the real model (spends); without
                                                 # a key it lists the cells and exits 2

uv run pytest -q                                 # 906 tests with node present; without node the 2 page
                                                 # tests in tests/test_page_js.py skip (904 passed, 2 skipped)
uv run ruff check .
uv run pyright
./scripts/check.sh                               # the three checks + scenarios + a replay sweep of all
                                                 # 53 archives + eval all; the proof in one command
```

The file `.github/workflows/ci.yml` exists and has **never been pushed and never run**; `scripts/check.sh` is the local equivalent that was actually executed. Do not claim the CI workflow passes.

## Invariants

These hold the design together. Do not break one as a side effect of another change. If a task needs one changed, say so and get agreement first. Each names what enforces it. Amendments and additions of versions 2.2 and 2.3 are [BLUEPRINT.md section 19.17](docs/BLUEPRINT.md#1917-invariants) and [section 20.13](docs/BLUEPRINT.md#2013-invariants).

| # | Invariant | Enforced by |
|---|---|---|
| 1 | The Auditor, the ledger and the Reporter make no model call. `run_role` in `kernel/runtime.py` is the only place an agent runs. | `test_architecture` |
| 2 | Judges may only veto. No judge output becomes a level or a rating. | The `Verdict` contract; `test_report` |
| 3 | Every number in a report comes from the ledger (`ledger/facts`, `fundamentals/facts`), from `tools/facts`, `macro/facts`, `portfolio/state` or a settled outcome — all computed by code — or from an audited proposal. Headlines and market probabilities are printed only as source wording, unverified. | `test_report` |
| 4 | The two computation paths (`indicators.py`/`recompute.py`, `fundamentals.py`/`fundamentals_sql.py`, `macro.py`/`macro_sql.py`, `memory.py`/`memory_sql.py`) each share no code and use only the standard library, and a fact or outcome exists only with both. Tool facts use the indicator pair. | `test_architecture`; `test_ledger`, `test_fundamentals`, `test_macro`, `test_memory`; `eval consistency` |
| 5 | A role sees exactly the keys in its `reads`. | `test_kernel`; `test_architecture` |
| 6 | No bar, fact, filing, item, headline, post, macro vintage, market, lesson or tool result dated after the analysis date reaches any stage. | `test_market`, `test_ledger`, `test_fundamentals`, `test_disclosures`, `test_news`, `test_social`, `test_macro`, `test_tools`, `eval pit` |
| 7 | A proposal that fails the audit is not delivered and not shown to a judge. A revised proposal (including the risk debate's revision) is audited again. | `test_flow`, `test_stage_portfolio_risk` |
| 8 | A finding goes to the earliest stage that owns it, and every later stage reruns. | `test_flow` |
| 9 | Refuse rather than guess. A `review` report has no rating and no level. | `test_flow`, `test_report` |
| 10 | Every model request is counted, and none is made once a ceiling or limit is passed. In parallel mode the whole analyst group is admitted before any branch starts. | `test_kernel`, `test_flow`, `test_graph` |
| 11 | The journal is the only source of truth; the board and every output can be rebuilt from it. Graph state holds only routing counters, so a checkpoint can always be checked against the journal. | `test_kernel`; `replay`, `test_graph` |
| 12 | Dependencies point inward, and each layer imports only the libraries it is allowed. `flows/graph.py` may import `langgraph` (and `langchain_core` only for `RunnableConfig`); `app/wiring.py` and `app/resume.py` may import the SQLite checkpointer; nothing else in `flows/` or `roles/` imports a third-party package. | `test_architecture` |
| 13 | The system never executes, routes or simulates an order. | No such code; stated in every report |
| 14 | No secret is written to the journal, a report or an exception message. Adapter errors pass through `scrub`. | `test_models`, `test_settings`, `test_server`, `test_vendors` |
| 15 | The web app listens on loopback only and runs one analysis at a time. The page shows only what is in the journal, the trace and the bars; it formats values and adds up usage and durations, and computes no fact. Run data is inserted as text, never as markup. | `test_server`, `test_run_page` |
| 16 | Every value that crosses a boundary is a frozen contract that rejects unknown fields. | `test_architecture` |
| 17 | With every v2.2 capability off (no channel among news/sentiment/macro, no portfolio, `risk_rounds = 0`, `tools = false`, `memory = false`, `approve = false`), a run writes the artifacts and sends the views version 2.1 does. `parallel` on or off changes only `ts` values. | `test_v22_switches`, `scenarios`, replay of the archived journals |
| 18 | A channel, source or lesson that could not be read is reported as unavailable or `none_known`, never as empty or missing silently, and the roles that read it are told which. Covers the v2.1 channels, `news`, `sentiment`, `macro`, the portfolio and the lessons. | `test_stages`, `test_report`, the scenarios `uncovered`, `quiet`, `news_uncovered` |
| 19 | Parallel and serial runs append the same journal, except `ts`. Timing lives in the trace, never in the journal. | `test_graph`, `test_trace` |
| 20 | No lesson whose outcome became known after the analysis date reaches any role, in `analyze` or `backtest`. | `test_memory`, `eval pit`, the scenario `memory_lessons` |
| 21 | A tool computes only through the indicator functions of both paths, on bars up to the analysis date, within declared ranges. | `test_tools`, `eval consistency`, `eval pit` |
| 22 | The trace (`<run dir>/trace.jsonl`) is measurement. No role reads it; no number in a report comes from it; `replay` and `resume` do not read its contents; deleting it changes no outcome. | `test_trace` |
| 23 | An observer cannot affect a run: with an observer that raises, the journal equals that of a run with none, and with no observer the kernel makes no extra clock call. | `test_kernel_v23` |
| 24 | An interrupted run is resumable from any point: after `resume`, a run interrupted between nodes ends with the board of an uninterrupted run. An interruption inside a node that makes several calls repeats that node's calls; the result is a valid run, not an identical journal. | `test_graph`, `test_flow_hardening` |

## Things that will surprise you

- **Reference tests are not weakened.** The tests that came from `docs/BLUEPRINT-REFERENCE.md` (`test_kernel`, `test_models`, `test_flow`, `test_architecture`, and `test_indicators`, which WP3 made registry-driven) define the kernel. Cases may be added; none may be removed or loosened. A failure means the new code is wrong. Do not widen `test_architecture`'s tables to let an import through.
- **The kernel was changed twice, by WP23 only (deferred commit and tools) and by WP40 only (the observer hook: `Run.observe` and its three guarded calls in `run_role_deferred`).** Nothing else in `insightcrew/kernel/` changes without the owner's agreement, and a further call of the hook (for tool calls, say) is not authorised.
- **Timing never goes into the journal.** Not as a field of `model.called`, not as a board key. It goes into `trace.jsonl`, written by the observer hook through `app/trace.py` for real command-line and web runs only (scenarios, tests and `replay` write none). A `role.started` line with no end is a call that was interrupted.
- **No `# noqa`, no `# type: ignore`.** The one in `tests/test_kernel.py` is deliberate. Warnings are errors in the test run, including deprecation warnings from libraries.
- **What you remember of Pydantic AI (2.68.0) and of LangGraph is probably out of date.** Read the installed source under `.venv/lib/python3.12/site-packages/` before using a call the code does not already make. Examples that bit: `SqliteSaver`'s async methods raise `NotImplementedError`, so the checkpointer is `AsyncSqliteSaver`; a node only receives `config["configurable"]` if its `config` parameter is annotated with `RunnableConfig` from `langchain_core`; `interrupt()` makes the node rerun from its start on `Command(resume=…)` (the approval gate relies on this).
- **The graph holds no data.** `flows/graph.py`'s state is four routing counters and a route; `Run`, `deps` and the request travel in `config["configurable"]`. Nodes commit their events at their end (parallel branches compute with `run_role_deferred` and the join commits in the fixed order technical, fundamentals, disclosures, news, sentiment — recomputing in place any due analyst whose result is missing after an interruption), so the journal never depends on which branch finished first. Do not put the board, contracts or `Run` into graph state; they do not belong in a checkpoint and break replay.
- **`stage_risk.risk` is `async`.** The risk debate awaits the kernel on the run's event loop; never reintroduce a thread bridge, or the server's loop blocks and a run cannot be cancelled.
- **`resume` checks a run signature** (ticker, date, the whole `FlowConfig`, the portfolio fingerprint, tier names; first 16 hex of its SHA-256 as the checkpoint thread ID). A different signature refuses with a Chinese message. Replay never uses the checkpointer and never writes the decision log. **Known gaps, left to the owner** (HANDOFF questions 38, 43, 44): `resume`'s model source ignores `IC_FORCE_OFFLINE` (a resume with a key in `.env` will spend), a run with a portfolio always fails the signature check on resume, and a run cancelled before its first checkpoint fails on resume with `EmptyInputError`.
- **Prompt changes cannot be checked offline.** The scripted model ignores prompts. A change under `roles/prompts/` needs a real-model run. `tests/test_prompts.py` checks prompt/role agreement, attribution-line removal and `unavailable` explanations; the stricter board-key reading it does not apply is HANDOFF question 36 (the owner's to settle).
- **A prompt file that adapts TradingAgents starts with its attribution line** (`<!-- Adapted from TradingAgents (Apache-2.0), modified. -->`); `roles/__init__.py`'s `_prompt` drops `<!--` lines, so the model never sees it. The list of adapted files is [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); version 2.3 added none.
- **Tests never touch the network.** `tests/conftest.py` blocks `socket.socket.connect`, `connect_ex` and `socket.getaddrinfo` unless the test is marked `integration`, and an autouse fixture removes every `*_API_KEY` environment variable for the length of a test. The live adapters (news RSS, StockTwits, Reddit, FRED, Polymarket) are tested against hand-built fixtures; **no live endpoint has been fetched yet** — each request is asked for first, and FRED also needs `IC_FRED_API_KEY` (without it, macro is `NotConfigured`).
- **Node runs tests only.** `tests/js/*.test.mjs` run under Node's built-in test runner through `tests/test_page_js.py`, which skips with a reason when `node` is absent. No `package.json`, no npm install, no bundler, ever.
- **The page is assembled, not built.** Its source is `insightcrew/app/assets/` (13 JS modules, 7 CSS files); `app/replay_html.py` splices them into one self-contained `page.html`/`replay.html` at the two marker lines. The whole page forbids `innerHTML`, `eval`, `new Function`, external resources, `href="…"`/`src="…"` in source and the string `http://`; links are set with `setAttribute`. `tests/test_run_page.py` enforces all of it and is not to be weakened.
- **The output ceiling includes reasoning tokens.** Both DeepSeek models reason by default. Lowering `max_output_tokens` in `roles/__init__.py` brings back truncated replies (`finish_reason: length`).
- **`.env` holds a real key.** Tests load settings with `force_offline=True` so that none builds a real model; keep it that way in new tests. Never print or commit `.env`.
- **`replay` and `scenarios` run in a temporary directory** and leave nothing in `runs/`. `analyze` and `serve` write to `runs/` (git-ignored), including `checkpoints.sqlite` and `trace.jsonl` per run and `runs/memory/` for the decision log.
- **Scenario numbers come from `facts`.** A scenario's claims must restate ledger values exactly, so regenerate them from `uv run insightcrew facts` after changing the synthetic data or an indicator.
- **Yahoo throttles.** `fetch` falls back to Eastmoney for bars. The source chains live in `[sources]` of `insightcrew.toml` and are tried in order by `first_ok` in `adapters/vendors.py`, which aggregates typed errors and scrubs keys from messages.
- **The SEC is only called with `IC_SEC_CONTACT` set** in `.env`; without it `fetch` skips the SEC and says so.
- **The 43 archived journals of versions 2.0 to 2.2 were made before** various prompt paragraphs (channels in 2.1; trader, manager, technical, bull, bear and judge in 2.2). The 10 archived with `trace.jsonl` (one pilot plus the nine `eval v22` cells) are from 2026-11-20 on version 2.3. All 53 replay `一致` and pass `verify`; the instructions are not compared.
- **Package installs on the owner's machine** can fail with a certificate error or HTTP 502 (a package-age block, or a conflict between two security tools). Do not retry, change versions or turn off verification; report it. The workspace rules describe the fix.

## Checking your work

Run the smallest relevant check, then widen it. Every change ends with the three checks clean (`uv run pytest -q`, `uv run ruff check .`, `uv run pyright`) and, for anything in the flow or the app, the proof of [BLUEPRINT 20.4](docs/BLUEPRINT.md#204-the-compatibility-rule) — `./scripts/check.sh` runs it all: the three checks, `uv run insightcrew scenarios` (36 lines ✓ 相符), a replay sweep of `insightcrew/evals/archive/` (53 of 53 一致) and `uv run insightcrew eval all` (exit 0).

| Change | Also run |
|---|---|
| Documentation only | Links resolve. Every number is either re-run or tied to an archived journal in `insightcrew/evals/archive/`. |
| `domain/indicators.py`, `recompute.py`, `data/indicators.toml`, `data/tool_indicators.toml` | `eval consistency`, `eval pit`, then `scenarios` |
| `domain/rules.py`, `audit.py`, `data/rules.toml` | `eval inject`, then `scenarios` |
| `domain/macro*.py`, `memory*.py`, `news.py`, `social.py`, `portfolio.py`, `risk.py` | The module's own tests, then `eval consistency`/`eval pit` where they apply |
| `flows/` (graph, stages, capability stages, approval), `app/wiring.py` | `test_graph`, `test_flow_hardening`, `test_v22_switches`, `scenarios`, `analyze --no-open`, then `replay` and `verify` on the run it wrote |
| `app/report.py`, `replay_html.py`, anything under `app/assets/`, `server.py` | `analyze --no-open` and open its `replay.html`; `serve` (with `IC_FORCE_OFFLINE=1`) and start `demo` and `contested`; the Node tests under `tests/js/` |
| `roles/`, `insightcrew.toml` `[tiers]` | `check`, `tests/test_prompts.py`, then one real-model `analyze ACME` (costs money; ask first); read the journal |
| Adapters for real bars or evidence | `tests/test_yahoo.py`, `tests/test_eastmoney.py`, `tests/test_edgar.py`, `tests/test_eastmoney_ann.py`, `tests/test_vendors.py` and the adapter's own tests; a live `fetch` only when asked |

Report which commands you ran and what they returned. If a check could not be run, say so and say what remains unverified.

## Working rules

- **Keep results honest.** Distinguish re-run from archived, scripted from real-model, synthetic from real bars. [BLUEPRINT.md sections 12.4](docs/BLUEPRINT.md#124-what-must-not-be-claimed), [18.17](docs/BLUEPRINT.md#1817-what-must-not-be-claimed), [19.20](docs/BLUEPRINT.md#1920-what-must-not-be-claimed) and [20.16](docs/BLUEPRINT.md#2016-what-goes-to-roadmap-and-what-must-not-be-claimed) list what must not be claimed — including that the page streams the model's output (it does not), that resume loses nothing (an interrupted call's usage is in no file; a multi-call node repeats its calls), that trace durations are a benchmark, that the approval gate is any regulatory control, that the CI workflow passes (it has never run), or that the page matches any named product.
- **Known issues are tracked, not fixed in passing.** Add them to [docs/ROADMAP.md](docs/ROADMAP.md); open questions go to [docs/HANDOFF.md](docs/HANDOFF.md) section 7. Questions 36 to 46 there are the owner's to settle; describe the current behaviour, do not settle them in code.
- **Keep the README a landing page.** Detail belongs in `docs/`.
- **Leave run output and secrets out of git.** `runs/`, `.env` and `data/market/real/` are ignored.
- **Do not commit or push unless asked.** When asked, add files by path. The discipline used throughout: one commit per finished work package on `v2.3`, after its acceptance passes, holding only that package's files, added by path (`git add <files>` then `git commit -m "WP<n>: <result>" -- <files>`); never `git add -A`, `git add .` or `git commit -a`; never push; never switch, stash, reset or rebase while others work. Delegated sessions that were not given git authorization leave their work uncommitted and say so; the coordinator commits it. A change needed in a file a package does not own is a request in HANDOFF section 7, not an edit.
- The sibling directories `TradingAgents-main/` and `open-multi-agent-main/` in the workspace are reference material. They are not part of this repository; do not edit them. Only the TradingAgents files listed in [BLUEPRINT.md section 19.4](docs/BLUEPRINT.md#194-adapting-tradingagents) may be adapted, each rewritten to this project's contracts with its header line and listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No LangChain import, and nothing from `open-multi-agent-main/`. Version 2.3 takes ideas only; it copies and adapts nothing.
- **Which files a package may touch** is [docs/HANDOFF.md](docs/HANDOFF.md) section 5.6; a change a package needs in a file it does not own is a request in that file's section 7, not an edit. Ask the owner before every network request and every real-model spend, naming the endpoint or the estimate.
