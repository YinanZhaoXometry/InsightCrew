# AGENTS.md

Working rules for AI coding agents in this repository. People should start with [README.md](README.md). The design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), results in [docs/EVALUATION.md](docs/EVALUATION.md), and known issues in [docs/ROADMAP.md](docs/ROADMAP.md). Link to those; do not repeat them here.

## What this is

InsightCrew version 2: a multi-agent trading assistant with an independent audit, for the COMP5584 group project. Given a ticker and a date, model roles (technical analyst, bull, bear, research manager, trader, three judges — plus, when the evidence channels are switched on, a fundamentals analyst and a disclosure analyst) produce a rating and a trade proposal. Code computes every fact twice, audits every cited number and every rule, and refuses with `REVIEW` when it cannot stand behind an answer.

- Python 3.12, managed by uv. The packages are those in `pyproject.toml` at the versions in `uv.lock`. **Do not add, remove or upgrade a package, or change `exclude-newer`, without the owner's agreement.**
- Run every command from this directory, through `uv run`.
- Code comments, docstrings, console output, prompts and reports are in Chinese. Enum values, keys and identifiers are in English. Documentation is in English.
- With no key in `.env`, every run without `--scenario` falls back to scripted replies, and the command says so. With a key, a run without a scenario calls the real model and costs money.
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md) is the specification and [docs/HANDOFF.md](docs/HANDOFF.md) the status log. Read both before a change that touches a name, a rule, a limit or an invariant.

## Commands

```bash
uv run insightcrew analyze [TICKER] [--date D] [--scenario NAME] [--universe U] [--channels fundamentals,disclosures] [--no-judges] [--debate-rounds N] [--quiet] [--no-open]
uv run insightcrew serve [--port N] [--no-open]     # local web app on 127.0.0.1
uv run insightcrew facts TICKER [--date D] [--universe U]   # ledger + what each channel would read
uv run insightcrew replay RUN_DIR                    # rerun from the journal with no model; expect 一致
uv run insightcrew scenarios [NAME]                  # expect 21 lines ✓ … 相符
uv run insightcrew check                             # settings without the key; probes each tier (needs a key)
uv run insightcrew fetch TICKER... [--years N]       # real bars + evidence into data/market/real/ (Yahoo→Eastmoney; SEC, then Eastmoney announcements)
uv run insightcrew eval all                          # inject 19/19+0/6 and 6/6+0/2, consistency 277×29 and 46×12, pit 864+16
uv run insightcrew eval ablation                     # 16 real-model runs; needs a key; about 0.5 USD
uv run insightcrew eval channels                     # 12 real-model runs across channel configurations; about 0.5 USD

uv run pytest -q                                     # 486 tests; no network, no key
uv run ruff check .
uv run pyright
```

## Invariants

These hold the design together. Do not break one as a side effect of another change. If a task needs one changed, say so and get agreement first. Each names what enforces it.

| # | Invariant | Enforced by |
|---|---|---|
| 1 | The Auditor, the ledger and the Reporter make no model call. `run_role` in `kernel/runtime.py` is the only place an agent runs. | `test_architecture` |
| 2 | Judges may only veto. No judge output becomes a level or a rating. | The `Verdict` contract; `test_report` |
| 3 | Every number in a report comes from the ledger (`ledger/facts` or `fundamentals/facts`) or from an audited proposal. Text quoted from a source (an item's kind and title) is printed from `disclosures/items` under a heading that says it is the source's wording and unverified. | `test_report` |
| 4 | The two computation paths (`indicators.py`/`recompute.py`, and `fundamentals.py`/`fundamentals_sql.py`) each share no code and use only the standard library, and an indicator or fundamentals fact exists only with both. | `test_architecture`; `test_ledger`, `test_fundamentals`; `eval consistency` |
| 5 | A role sees exactly the keys in its `reads`. | `test_kernel`; `test_architecture` |
| 6 | No bar, fact, filing or item dated after the analysis date reaches any stage. | `test_market`, `test_ledger`, `test_fundamentals`, `test_disclosures`, `eval pit` |
| 7 | A proposal that fails the audit is not delivered and not shown to a judge. A revised proposal is audited again. | `test_flow` |
| 8 | A finding goes to the earliest stage that owns it, and every later stage reruns. | `test_flow` |
| 9 | Refuse rather than guess. A `review` report has no rating and no level. | `test_flow`, `test_report` |
| 10 | Every model request is counted, and none is made once a ceiling or limit is passed. | `test_kernel`, `test_flow` |
| 11 | The journal is the only source of truth; the board and every output can be rebuilt from it. | `test_kernel`; `replay` |
| 12 | Dependencies point inward, and each layer imports only the libraries it is allowed. `flows/` and `roles/` import none. | `test_architecture` |
| 13 | The system never executes, routes or simulates an order. | No such code; stated in every report |
| 14 | No secret is written to the journal, a report or an exception message. | `test_models`, `test_settings`, `test_server` |
| 15 | The web app listens on loopback only and runs one analysis at a time. The page inserts run data as text, never as markup. | `test_server`, `test_run_page` |
| 16 | Every value that crosses a boundary is a frozen contract that rejects unknown fields. | `test_architecture` |
| 17 | With no channel switched on, a run writes the artifacts and sends the prompts version 2.0 does. | `test_stages`, `scenarios`, replay of the archived journals |
| 18 | A channel that could not be read is reported as unavailable, never as empty, and the roles that read it are told which. | `test_stages`, `test_report`, the scenarios `uncovered` and `quiet` |

## Things that will surprise you

- **Reference tests are not weakened.** The tests that came from `docs/BLUEPRINT-REFERENCE.md` (`test_kernel`, `test_models`, `test_flow`, `test_architecture`, and `test_indicators`, which WP3 made registry-driven) define the kernel. Cases may be added; none may be removed or loosened. A failure means the new code is wrong. Do not widen `test_architecture`'s tables to let an import through. `insightcrew/kernel/` is frozen.
- **No `# noqa`, no `# type: ignore`.** The one in `tests/test_kernel.py` is deliberate. Warnings are errors in the test run, including deprecation warnings from libraries.
- **What you remember of Pydantic AI is probably out of date.** The version is 2.68.0. Read the installed source under `.venv/lib/python3.12/site-packages/pydantic_ai/` before using a call the code does not already make.
- **Prompt changes cannot be checked offline.** The scripted model ignores prompts. A change under `roles/prompts/` needs a real-model run. If you cannot run one, say the change is untested.
- **The output ceiling includes reasoning tokens.** Both DeepSeek models reason by default. Lowering `max_output_tokens` in `roles/__init__.py` brings back truncated replies (`finish_reason: length`).
- **`.env` holds a real key.** Tests load settings with `force_offline=True` so that none builds a real model; keep it that way in new tests. Never print or commit `.env`.
- **`replay` and `scenarios` run in a temporary directory** and leave nothing in `runs/`. `analyze` and `serve` write to `runs/`, which git ignores.
- **Scenario numbers come from `facts`.** A scenario's claims must restate ledger values exactly, so regenerate them from `uv run insightcrew facts` after changing the synthetic data or an indicator.
- **Yahoo throttles.** `fetch` falls back to Eastmoney. Tests never touch the network; they replay `tests/fixtures/`.
- **The SEC is only called with `IC_SEC_CONTACT` set** in `.env`; without it `fetch` skips the SEC and says so. The channels default to off: `insightcrew.toml` deliberately sets none.
- **The 30 version-2.0 archived journals were made before the prompts of bull, bear, manager and judge gained their channels paragraph** (WP16); the 13 version-2.1 journals after. They all replay; the prompts are not compared.
- **Package installs on the owner's machine** can fail with a certificate error or HTTP 502 (a package-age block, or a conflict between two security tools). Do not retry, change versions or turn off verification; report it. The workspace rules describe the fix.

## Checking your work

Run the smallest relevant check, then widen it. Every change ends with the three checks clean: `uv run pytest -q`, `uv run ruff check .`, `uv run pyright`.

| Change | Also run |
|---|---|
| Documentation only | Links resolve. Every number is either re-run or tied to an archived journal in `insightcrew/evals/archive/`. |
| `domain/indicators.py`, `recompute.py`, `data/indicators.toml` | `eval consistency`, `eval pit`, then `scenarios` |
| `domain/rules.py`, `audit.py`, `data/rules.toml` | `eval inject`, then `scenarios` |
| `flows/`, `app/wiring.py` | `scenarios`, `analyze --no-open`, and `replay` on the run it wrote |
| `app/report.py`, `replay_html.py`, `assets/page.html`, `server.py` | `analyze --no-open` and open its `replay.html`; `serve` and start `demo` and `contested` |
| `roles/`, `insightcrew.toml` `[tiers]` | `check`, then one real-model `analyze ACME` (costs money); read the journal |
| Adapters for real bars or evidence | `tests/test_yahoo.py`, `tests/test_eastmoney.py`, `tests/test_edgar.py`, `tests/test_eastmoney_ann.py`; a live `fetch` only when asked |

Report which commands you ran and what they returned. If a check could not be run, say so and say what remains unverified.

## Working rules

- **Keep results honest.** Distinguish re-run from archived, scripted from real-model, synthetic from real bars. [BLUEPRINT.md section 12.4](docs/BLUEPRINT.md#124-what-must-not-be-claimed) lists what must not be claimed.
- **Known issues are tracked, not fixed in passing.** Add them to [docs/ROADMAP.md](docs/ROADMAP.md).
- **Keep the README a landing page.** Detail belongs in `docs/`.
- **Leave run output and secrets out of git.** `runs/`, `.env` and `data/market/real/` are ignored.
- **Do not commit or push unless asked.** When asked, add files by path.
- The sibling directories `TradingAgents-main/` and `open-multi-agent-main/` in the workspace are reference material. They are not part of this repository; do not edit them or copy code from them.
