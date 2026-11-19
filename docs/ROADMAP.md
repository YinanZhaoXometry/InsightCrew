# Roadmap

What version 2 left out, what the evaluation says about the parts that could be removed, and what to build next. Version 1, the data analyst, is preserved at the tag `v1-data-analyst`.

## 1. What was cut

These were in the design and were left out to fit the time. Each leaves a coherent system. Where the code already has a place waiting for the work, it is named.

| Work | State | What it would take |
|---|---|---|
| **Portfolio** | Not built. Every run sees an empty account: the current position is 0% and cash is unknown. `sell_requires_position` therefore always fails a `Sell`, and `cash_sufficient` never applies | A JSON file with currency, cash, equity and positions. Intake writes `portfolio/state`, which the Trader already reads, and passes `position_pct` and `cash_pct` to the rules, which already accept them. Three states to test: none, flat, held |
| **News analyst** | Not built. The analysis uses price and volume only | Dated news fixtures per ticker behind a port with the same as-of rule as bars; an "unavailable" marker distinct from "no news"; the analysts as a parallel stage. [TRANSFORMATION.md](TRANSFORMATION.md) sections 4 and 7 |
| **Memory** | Not built. The Manager reads `memory/lessons`, which nothing writes | A decision log; a settler in code that scores each past decision once its outcome is known; a model that writes lessons; lessons filtered by the date their outcome became known, so no lesson leaks the future. A second flow, `flows/settle.py`, and a backtest grid under batch ceilings. TRANSFORMATION.md section 8 |
| **Tools for roles (autonomy L1)** | Designed, not tried. Every role fills its contract in one call from a fixed ledger | `Role` gains `tools`; `run_role` passes them to the agent with a request limit. Tools compute through the indicator registry, so every tool fact still has its second path. A kernel change. [BLUEPRINT.md section 11.2](BLUEPRINT.md#112-the-dials) |
| **A check on numbers in prose** | Not built. The audit reads structured fields only | Flag numbers in narratives that match no fact, as warnings rather than findings |

## 2. What the evaluation says about the scaffolding

Part of the system exists because current models make particular mistakes. The design said each such part should go when it stops earning its cost, and that the evaluation would say when ([BLUEPRINT.md section 11.3](BLUEPRINT.md#113-scaffolding-and-how-to-tell-when-to-remove-it)). After the runs in [EVALUATION.md](EVALUATION.md):

| Piece | Evidence so far | Verdict |
|---|---|---|
| Corrective retry | 0.25 retries per run in the full configuration; it recovered two imitation replies in the early runs | Keep |
| Claims restate the fact's value | `claim_value_matches_fact` never fired in 29 real runs | Candidate for removal (cite by ID only), once more runs agree |
| Bull and bear debate | Removing it changed two of four ratings, which is within the run-to-run noise | Undecided. Needs more cells |
| Three judges | They changed one outcome in four cells, and refused a thesis that contradicted its own levels | Keep. They are most of the cost, so a cheaper second model for one lens is worth trying |
| Quick and deep tiers | Not varied | Untested |
| Fact ledger with two paths, rulebook and audit, views, point in time, budgets, journal | | The guarantee. Never removed. That the audit never fired with this model does not make it optional |

## 3. Next

In order of value for the claim the system makes:

1. **More real-model cells.** Four cells cannot separate any configuration from chance. Twenty cells × four configurations would cost about 2.5 USD at the measured rates, and would settle the debate question.
2. **The portfolio.** It makes two rules meaningful and the Trader's sizing real.
3. **A second model family.** Every result is from DeepSeek. Any provider that speaks the OpenAI protocol needs only `IC_BASE_URL` and a name in `insightcrew.toml`. A different model as one judge would also test whether three judges sharing one model share its blind spots.
4. **Structured output in `native` mode.** The library refuses it for these models today. When a provider supports it, corrective retries should fall to zero, and the retry can then be removed.
5. **Memory and the backtest grid.** The first step toward saying anything about decision quality, and the largest piece of work.

## 4. Known rough edges

- `start.bat` was written for Windows but has not been run on Windows.
- A bar file with two rows for the same date is not rejected when it is read (`adapters/market_csv.py`), so `recompute.load` raises an uncaught `sqlite3.IntegrityError` on its primary key. The committed data has no duplicates and both fetchers write one row per date, but a hand-edited CSV could trigger it. The fix is to reject duplicate dates in the reader, naming the file and the date.
- `_one_line` is defined twice, in `app/check.py` and `app/replay.py`.
- Downloaded bars are not committed, so the two archived real-bar runs replay only after `uv run insightcrew fetch AAPL 0700.HK --years 3`.
- Yahoo throttled every request from the development machine. Eastmoney works, but both are unofficial endpoints.
- Instrument names from Eastmoney are in Chinese, and Yahoo's in English, so the same ticker can carry a different name depending on which source answered.
- Without uv, `pip install -e .` resolves newer library versions than `uv.lock` pins. It was tried once on Python 3.15.2, and the offline demo ran.
