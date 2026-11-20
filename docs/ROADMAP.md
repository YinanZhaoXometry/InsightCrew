# Roadmap

What version 2.3 leaves open, what the evaluation says about the parts that could be removed, and what to build next. Version 1, the data analyst, is preserved at the tag `v1-data-analyst`; version 2.0 at `v2.0`; version 2.1 at `v2.1`; version 2.2 at `v2.2`. Version 2.3 is built and green (906 tests with Node present, 36 scenarios, 53 archived journals replaying `一致` and passing `verify`), and its real-model runs are done (one pilot plus the nine cells, 0.3174 USD of the 2 USD cap). What has deliberately never run: any live endpoint of the five new sources, and the CI workflow file.

## 1. What is pending

Built, tested offline, and waiting for a decision or a permission:

| Work | State | What it waits for |
|---|---|---|
| **The live endpoints of the five new sources** | Google News RSS, Yahoo Finance RSS, StockTwits, Reddit, Polymarket: adapters built and tested against hand-built fixtures; FRED's adapter likewise, and it needs `IC_FRED_API_KEY` (not yet in `.env`) | Per-endpoint approval (the owner is asked before every network request, naming the endpoint). After approval: one live request per source, its response saved as a fixture, then `fetch --news --social --macro --markets` to start the archives |
| **The v2.1 evidence downloads** | The adapters are offline-tested; the live run was blocked because Eastmoney's bars endpoint closed the connection (`Server disconnected`) before the evidence stage began | The network, and the same per-request approval for the SEC |
| **Decide the switches** | `insightcrew.toml` today switches no capability on and leaves the approval gate off | The owner, after reading the cells of [EVALUATION.md section 3c](EVALUATION.md#3c-the-version-22-cells-on-a-real-model) |
| **The open questions of the build** | Questions 36 to 46 of [HANDOFF.md](HANDOFF.md) section 7: the prompt-integrity finding on `manager.md`'s `memory/status` sentence (36); `resume`'s model source ignoring `IC_FORCE_OFFLINE` (38) and the two known resume gaps — a run with a portfolio fails the signature check (43), a run cancelled before its first checkpoint fails on resume with `EmptyInputError` (44); the approval gate's two label placements (45) and the two one-line test changes that rode with it (46); and the readings recorded in 39 to 42 | The owner's review. The code follows each row's recorded reading until then |
| **The behaviour questions BLUEPRINT 20.7 left open** | The prediction-markets reader in `flows/stage_news.py` reads files from a flow stage and treats an unreadable file as no markets; `audit/channels` is written only when the fundamentals or disclosures channel is present; the news analyst is skipped when the news window is empty even if macro facts are available | Each needs a decision about behaviour, not only a repair |

Nothing was cut from version 2.3: every work package of BLUEPRINT 20.14 was built, including the stretch approval gate.

## 2. What the evaluation says about the scaffolding

Part of the system exists because current models make particular mistakes. The design said each such part should go when it stops earning its cost, and that the evaluation would say when ([BLUEPRINT.md section 11.3](BLUEPRINT.md#113-scaffolding-and-how-to-tell-when-to-remove-it)). After the runs in [EVALUATION.md](EVALUATION.md):

| Piece | Evidence so far | Verdict |
|---|---|---|
| Corrective retry | 0.25 retries per run in the full v2.0 configuration; two of 102 calls in the v2.3 cells, both absorbed by the SDK's own retry | Keep |
| Claims restate the fact's value | `claim_value_matches_fact` never fired in 39 real runs that reached the audit | Candidate for removal (cite by ID only), once more runs agree |
| Bull and bear debate | Removing it changed two of four ratings in the v2.0 ablation, which is within the run-to-run noise | Undecided. Needs more cells |
| Three judges | They changed one outcome in four ablation cells, refused a thesis that contradicted its own levels, and ended one v2.3 cell in `contested` | Keep. They are most of the cost, so a cheaper second model for one lens is worth trying |
| Three-way risk debate | Ran in four cells and the pilot (`acme_risk_on`, `aapl_risk_on`, both all-on runs); every revised proposal passed the audit | Runs and its cost is in the cells table; whether it earns its cost is not measured (a deliberate ablation needs more cells) |
| Evidence channels (2.1) | Mechanisms verified on a real model; citations routed and corrected; cost about 2.4× with both on | Off by default. Turn on per run with `--channels`; more cells before it could be a default |
| News, macro, sentiment channels (2.2) | Offline checks exact (citations, bands, point-in-time); ran available in the four ACME all-on runs with clean output and cited evidence | Off by default, pending the live archives (without them, real tickers see the channels `unavailable`) |
| Analyst tools (2.2) | Both paths agree on 63 calls; a leaky port changes nothing; tool facts audited. On the real model the ledger already held the facts, so DeepSeek never called the tool | Mechanism proven offline; a real exercise needs a prompt change (the owner's to order) |
| Memory, settle, backtest (2.2) | Two-path settlement, locked log, three-date scripted backtest with resume; the cells wrote decisions idempotently | Mechanism proven; no real settlement has run (the decisions are pending their windows) |
| Parallel analysts | One trace shows the five-analyst group overlapping: 48.0 s of calls in a 13.7 s span | Works and saves wall time on the group; the one measurement is not a benchmark |
| Approval gate (2.3) | Two scripted scenarios, browser accept and reject, both replay `一致` | Off by default; nobody has used it for real |
| Quick and deep tiers | Not varied | Untested |
| Fact ledger with two paths (four pairs), rulebook and audit, views, point in time, budgets, journal and trace, the graph's commit discipline, `verify` | | The guarantee. Never removed. That the audit never fired with this model does not make it optional |

## 3. Next

In order of value for the claim the system makes:

1. **Endpoint approvals, then the archives.** One live request per new source, saved as a fixture; then `fetch --news --social --macro --markets` on a schedule, so the archives grow a history and historical runs become honestly covered instead of `unavailable`. Retry the v2.1 evidence download at the same time.
2. **Decide the switches.** The owner decides which capabilities and whether the approval gate `insightcrew.toml` turns on by default (today: none).
3. **More real-model cells.** Four cells per configuration cannot separate anything from chance. Twenty cells × four configurations would cost about 2.5 USD at the measured rates, and would settle the debate question and the risk-debate ablation. A "all capabilities, serial" cell (about 0.05 USD) would measure end-to-end wall time against `acme_all_on` more directly than the minimal pair did.
4. **Settle the open questions** of HANDOFF section 7 — above all the three resume gaps (38, 43, 44), which are the sharpest edges an interrupted run can meet.
5. **A second model family.** Every result is from DeepSeek. Any provider that speaks the OpenAI protocol needs only `IC_BASE_URL` and a name in `insightcrew.toml`. A different model as one judge would also test whether three judges sharing one model share its blind spots.
6. **Structured output in `native` mode.** The library refuses it for these models today. When a provider supports it, corrective retries should fall to zero, and the retry can then be removed.
7. **A check on numbers in prose.** The audit reads structured fields only; flagging numbers in narratives that match no fact (as warnings, not findings) was cut from version 2 and is still not built.
8. **Push and run the CI workflow.** The file exists and has never been pushed or run; the local proof is `scripts/check.sh`. Pushing is the owner's decision alone.

## 4. Not built in this version

BLUEPRINT 20.16's list — deliberately not built in version 2.3, recorded here so nothing is claimed that is not there:

- **Token streaming.** A call's output appears only when it returns; the page shows liveness (who is working, for how long), not partial output.
- **Live notices of tool calls.** A further call of the kernel's observer hook, which needs the owner's word; the kernel is otherwise frozen.
- **The three behaviour questions** at the end of BLUEPRINT 20.7, listed in section 1 above.
- **A manager prompt that guards against speaking-order bias** — the condition was "if a WP38 journal shows the bias"; no journal of the nine cells and the pilot showed it, so nothing is pending and no prompt is changed.
- **Insider transactions as a channel.**
- **`Retry-After` handling in the data adapters.**
- **Lenient numeric coercion of model output** (a number that arrives as a string, say); today that is a malformed reply and the corrective retry's job.
- **Follow-up questions on a finished run.**
- **Forking a run from a stage** (re-running from a chosen point with different settings).
- **Comparing two runs side by side.**
- **Redacting a journal for sharing.**
- **Flushing the journal to disk on every append** (today an interrupted process can leave a torn last line, which `resume` repairs by truncating to the last complete line).

## 5. Known rough edges

- `start.bat` was written for Windows but has not been run on Windows.
- A bar file with two rows for the same date is not rejected when it is read (`adapters/market_csv.py`), so `recompute.load` raises an uncaught `sqlite3.IntegrityError` on its primary key. The committed data has no duplicates and both fetchers write one row per date, but a hand-edited CSV could trigger it. The fix is to reject duplicate dates in the reader, naming the file and the date.
- Downloaded bars are not committed, so the four archived real-bar runs replay only after `uv run insightcrew fetch AAPL 0700.HK --years 3`.
- Yahoo throttled every request from the development machine. Eastmoney answered during version 2, but during the 2.1 build its bars endpoint closed the connection twice without a response; the announcement endpoint was probed and answered 200 earlier the same day. Both are unofficial endpoints. None of the five version 2.2 sources has been tried live.
- The SEC download was never exercised live: it needs `IC_SEC_CONTACT` (which is set locally), but the bars stage of the same `fetch` command failed first. The adapter is tested against saved responses.
- Instrument names from Eastmoney are in Chinese, and Yahoo's in English, so the same ticker can carry a different name depending on which source answered.
- Without uv, `pip install -e .` resolves newer library versions than `uv.lock` pins. It was tried once on Python 3.15.2 (before version 2.2), and the offline demo ran. Version 2.2 adds LangGraph to the dependency set; that path has not been retried since.
- The prediction-market point-in-time rule (`fetched_on == as_of`) is implemented in two private places, `flows/stage_news.py` and `evals/pit.py`, each documented to change with the other; consolidating them in one module is a small refactor for the owner to commission (HANDOFF question 30).
- `resume`'s model source ignores `IC_FORCE_OFFLINE` (HANDOFF question 38): with a key in `.env`, a resume will spend on the real model even when the server was started offline. Two related gaps: a run with a portfolio always fails the signature check on resume (question 43), and a run cancelled before its first checkpoint fails on resume with `EmptyInputError` (question 44). All three wait on the owner; `AGENTS.md` warns about them.
- With the approval gate on, a rejected run's `report.md` prints the raw reason key `rejected_by_user` (the Chinese label lives on the page), and the judgment label reads "not reviewed" even when the judges had accepted. Both placements are recorded for the owner (HANDOFF question 45); each is a one-line change.
