# Roadmap

What version 2.2 leaves open, what the evaluation says about the parts that could be removed, and what to build next. Version 1, the data analyst, is preserved at the tag `v1-data-analyst`; version 2.0 at `v2.0`; version 2.1 at `v2.1`. Version 2.2 is built and green (825 tests, 34 scenarios, 43 archived journals replaying `一致`), but two of its pieces have deliberately not run: its real-model cells and its live endpoints.

## 1. What is pending

Built, tested offline, and waiting for a decision or a permission:

| Work | State | What it waits for |
|---|---|---|
| **The nine real-model cells (WP38)** | Defined in `evals/v22.py`; `eval v22` lists them and exits 2. The cells: ACME with all capabilities on, all off, risk only, tools only, a portfolio; AAPL with risk and with tools; 0700.HK with everything; one serial run for wall time | The owner's spending cap (HANDOFF question 14). The capability ablation — whether the risk debate and the three judges both earn their cost (BLUEPRINT decision 45) — waits on the same cells |
| **The live endpoints of the five new sources** | Google News RSS, Yahoo Finance RSS, StockTwits, Reddit, Polymarket: adapters built and tested against hand-built fixtures; FRED's adapter likewise, and it needs `IC_FRED_API_KEY` (not yet in `.env`) | Per-endpoint approval (the owner is asked before every network request, naming the endpoint). After approval: one live request per source, its response saved as a fixture, then `fetch --news --social --macro --markets` to start the archives |
| **The v2.1 evidence downloads** | The adapters are offline-tested; the live run was blocked because Eastmoney's bars endpoint closed the connection (`Server disconnected`) before the evidence stage began | The network, and the same per-request approval for the SEC |
| **Per-analyst wall time in artifacts** | Measured per run in memory only; `run/wall_ms` is not written to the board, the journal, the report or the page. `app/replay.py`'s `_diff` already ignores the key, so the day it is persisted only two reference tests need their expected-boards amended | HANDOFF question 20, parked at option C. Option A (persist `run/wall_ms` as a board key) was tried and backed out: it collided with the reference tests' whole-board comparison and `board.json` byte comparison |
| **A resumed run is not marked** | The page and report cannot show that a run was resumed, because the journal has no event type for it and the event set is closed | A kernel change (a new event type), which needs the owner's authorisation |
| **The version 2.2 open questions** | About twenty settled-by-recommendation readings and pending questions from the overnight build: the copyright line of the notices (15), macro transform formulas (16), macro coverage semantics (17, 18), the header's placement in `data/macro.toml` (19), same-bar stop/target (21), settlement without a benchmark (22), Sell's hit (23), the two-path interface (24), Polymarket's search term (25), the announcements chain's semantics (26), scenario `memory: []` (32), and others | The owner's review. The live list, with each question's recommended reading, is [HANDOFF.md](HANDOFF.md) section 7 |

Nothing was cut from version 2.2: all five capabilities, the graph, the evaluations, the report, the page and the commands are built. What was cut from earlier versions — the portfolio, tools, memory, a media news analyst, and the two-path guarantees around them — is this version's body.

## 2. What the evaluation says about the scaffolding

Part of the system exists because current models make particular mistakes. The design said each such part should go when it stops earning its cost, and that the evaluation would say when ([BLUEPRINT.md section 11.3](BLUEPRINT.md#113-scaffolding-and-how-to-tell-when-to-remove-it)). After the runs in [EVALUATION.md](EVALUATION.md):

| Piece | Evidence so far | Verdict |
|---|---|---|
| Corrective retry | 0.25 retries per run in the full configuration; it recovered two imitation replies in the early runs | Keep |
| Claims restate the fact's value | `claim_value_matches_fact` never fired in 29 real runs | Candidate for removal (cite by ID only), once more runs agree |
| Bull and bear debate | Removing it changed two of four ratings, which is within the run-to-run noise | Undecided. Needs more cells |
| Three judges | They changed one outcome in four cells, and refused a thesis that contradicted its own levels | Keep. They are most of the cost, so a cheaper second model for one lens is worth trying |
| Three-way risk debate | Built; every test and scenario green; no real-model run yet | Unmeasured. The `acme_risk_on` / `aapl_risk_on` cells and the decision-45 ablation are pending |
| Evidence channels (2.1) | Mechanisms verified on a real model; citations routed and corrected; cost about 2.4× with both on; four cells cannot say more | Off by default. Turn on per run with `--channels`; more cells before it could be a default |
| News, macro, sentiment channels (2.2) | Offline checks exact (citations, bands, point-in-time); no real-model run | Off by default, pending the cells and the live archives |
| Analyst tools (2.2) | Both paths agree on 63 calls; a leaky port changes nothing; tool facts audited | Unmeasured on a real model (`acme_tools_on`, `aapl_tools_on` pending) |
| Memory, settle, backtest (2.2) | Two-path settlement, locked log, three-date scripted backtest with resume; no real-model run | Unmeasured on a real model |
| Quick and deep tiers | Not varied | Untested |
| Fact ledger with two paths (now four pairs), rulebook and audit, views, point in time, budgets, journal, the graph's commit discipline | | The guarantee. Never removed. That the audit never fired with this model does not make it optional |

## 3. Next

In order of value for the claim the system makes:

1. **The spending cap, then the nine cells (WP38).** One pilot, prompts read and fixed one at a time tied to a journal, then `eval v22`. Everything about the capabilities that matters — do they run, what do they cost, does parallel help — is behind this.
2. **Endpoint approvals, then the archives.** One live request per new source, saved as a fixture; then `fetch --news --social --macro --markets` on a schedule, so the archives grow a history and historical runs become honestly covered instead of `unavailable`. Retry the v2.1 evidence download at the same time.
3. **Decide the switches.** After the cells, the owner decides which capabilities `insightcrew.toml` turns on by default (today: none).
4. **More real-model cells.** Four cells per configuration cannot separate anything from chance. Twenty cells × four configurations would cost about 2.5 USD at the measured v2.0 rates, and would settle the debate question and the risk-debate ablation.
5. **Persist wall time (question 20, option A)** once its two reference-test amendments are authorised, so the parallel/serial comparison lives in artifacts.
6. **A second model family.** Every result is from DeepSeek. Any provider that speaks the OpenAI protocol needs only `IC_BASE_URL` and a name in `insightcrew.toml`. A different model as one judge would also test whether three judges sharing one model share its blind spots.
7. **Structured output in `native` mode.** The library refuses it for these models today. When a provider supports it, corrective retries should fall to zero, and the retry can then be removed.
8. **A check on numbers in prose.** The audit reads structured fields only; flagging numbers in narratives that match no fact (as warnings, not findings) was cut from version 2 and is still not built.

## 4. Known rough edges

- `start.bat` was written for Windows but has not been run on Windows.
- A bar file with two rows for the same date is not rejected when it is read (`adapters/market_csv.py`), so `recompute.load` raises an uncaught `sqlite3.IntegrityError` on its primary key. The committed data has no duplicates and both fetchers write one row per date, but a hand-edited CSV could trigger it. The fix is to reject duplicate dates in the reader, naming the file and the date.
- `_one_line` is defined twice, in `app/check.py` and `app/replay.py` (`app/resume.py` imports the second).
- Downloaded bars are not committed, so the two archived real-bar runs replay only after `uv run insightcrew fetch AAPL 0700.HK --years 3`.
- Yahoo throttled every request from the development machine. Eastmoney answered during version 2, but during the 2.1 build its bars endpoint closed the connection twice without a response; the announcement endpoint was probed and answered 200 earlier the same day. Both are unofficial endpoints. None of the five version 2.2 sources has been tried live.
- The SEC download was never exercised live: it needs `IC_SEC_CONTACT` (which is set locally), but the bars stage of the same `fetch` command failed first. The adapter is tested against saved responses.
- Instrument names from Eastmoney are in Chinese, and Yahoo's in English, so the same ticker can carry a different name depending on which source answered.
- Without uv, `pip install -e .` resolves newer library versions than `uv.lock` pins. It was tried once on Python 3.15.2 (before version 2.2), and the offline demo ran. Version 2.2 adds LangGraph to the dependency set; that path has not been retried since.
- The prediction-market point-in-time rule (`fetched_on == as_of`) is implemented in two private places, `flows/stage_news.py` and `evals/pit.py`, each documented to change with the other; consolidating them in one module is a small refactor for the owner to commission (HANDOFF question 30).
