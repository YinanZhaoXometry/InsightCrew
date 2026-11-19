# Evaluation

What InsightCrew version 2.2 was tested on, what happened, and what the results do and do not support. Every number here comes from a command you can re-run or a journal archived in `insightcrew/evals/archive/`. Commands are run from the repository root. The real-model journals are all from versions 2.0 and 2.1; the nine version 2.2 real-model cells are defined and **not run** (section 3c).

## Contents

1. [What is being evaluated](#1-what-is-being-evaluated)
2. [Offline evaluations](#2-offline-evaluations)
3. [Real-model runs](#3-real-model-runs)
4. [What the real runs changed](#4-what-the-real-runs-changed)
5. [Ablation](#5-ablation)
6. [Failure cases and observations](#6-failure-cases-and-observations)
7. [What these results do not show](#7-what-these-results-do-not-show)
8. [Reproducing](#8-reproducing)

## 1. What is being evaluated

The system defends one claim: **no number reaches the user unchecked, and no proposal that breaks a stated limit is delivered.** The evaluation is built around that claim, in two parts:

- **The mechanism, offline.** Does each gate catch what it should, and nothing it should not? Do the two computation paths agree, for every pair including the new macro and settlement pairs? Does anything dated after the analysis date ever get through, in any of the eight point-in-time sweeps? These run with no model and no network, so the results are exact and anyone can reproduce them.
- **Behaviour with a real model.** Does the flow complete with a real model, what does it cost, how often is work sent back, and what does each part of the verification change? These results come from DeepSeek models, archived as journals.

It does not evaluate whether the ratings are good trades. Section 7 explains why it cannot.

## 2. Offline evaluations

`uv run insightcrew eval all` runs the first three blocks and exits 0; `uv run insightcrew scenarios` runs the fourth.

| Evaluation | Result |
|---|---|
| Fault injection (`eval inject`) | 19 of 19 injected faults caught, 0 of 6 valid variants flagged; with both v2.1 channels 6 of 6 caught, 0 of 2 flagged; version 2.2 faults 6 of 6 caught, 0 of 2 flagged |
| Two-path consistency (`eval consistency`) | Indicators 277 data points × 29 declared facts, all equal; fundamentals 46 dates × 12 facts, all equal; tools 63 calls × two paths (7 families × 3 analysis dates × 3 windows), all equal; macro 96 points × two paths, all equal |
| Point in time (`eval pit`) | 18 data points, 864 ledger facts: later bars change no fact; channels 16 data points; then six version 2.2 sweeps — news 9, social 9, macro 91, prediction markets 6, lessons 5, tools 63 — all clean |
| Scenarios (`scenarios`) | 34 of 34 scripted runs end as specified (21 from versions 2.0/2.1, 13 for the version 2.2 capabilities) |
| Replay of the archive | 43 of 43 archived journals replay `一致` (`IC_FORCE_OFFLINE=1`, exit 0 each) |
| Tests (`pytest`) | 825 passed |

**Fault injection.** Starts from the passing board of the `clean` scenario and changes one thing at a time. Each fault must produce a finding with a specific check:

| # | Fault | Caught by |
|---|---|---|
| 1 | A claim's value × 1.03 | `claim_value_matches_fact` |
| 2 | A claim's value replaced by another fact's value | `claim_value_matches_fact` |
| 3 | A claim cites `rsi_15`, which does not exist | `claim_cites_known_fact` |
| 4 | A thesis point cites a fact no claim cites | `point_cites_claimed_fact` |
| 5, 6 | Thesis horizon 0, and 500 | `horizon_in_range` |
| 7 | Stop above entry | `stop_side` |
| 8 | Stop missing | `stop_required` |
| 9 | Size raised until the risk on the trade exceeds 1% | `max_risk_per_trade_pct` |
| 10 | Size 25% with risk still within the limit | `max_position_pct` |
| 11 | Target set for a reward-to-risk of exactly 1.0 | `min_reward_risk` |
| 12 | Stop only 0.3 × ATR below entry | `min_stop_distance_atr` |
| 13 | Entry at close + 5 × ATR | `entry_near_market` |
| 14 | `Buy` under a `Hold` rating | `action_matches_rating` |
| 15 | `Hold` with a 5% size | `hold_is_flat` |
| 16 | `Sell` with no position held | `sell_requires_position` |
| 17 | The proposal cites an unknown fact | `proposal_cites_known_fact` |
| 18 | Proposal horizon differs from the thesis | `horizon_matches_thesis` |
| 19 | Entry, stop and target all divided by 100 | `entry_near_market` |

The six valid variants sit exactly on a limit, or omit something optional: risk exactly 1%; stop exactly 1.0 × ATR below entry; reward-to-risk exactly 1.5; entry exactly 2.0 × ATR from the close; no target; a claim's value rounded to two decimals. None was flagged.

The version 2.1 channels add six channel faults (a fundamentals claim citing a fact no one claimed; a signal citing a ghost item; all signals deleted; thesis points citing unread items and unclaimed fundamentals facts) — 6 of 6 caught, 0 of 2 valid variants flagged.

The version 2.2 block runs through the real portfolio and analysis chain (the portfolios of `data/portfolios/`, the macro facts of the synthetic archive):

| # | Fault | Caught by |
|---|---|---|
| 1 | The portfolio flipped from `flat` to `held` under the same Buy proposal | `max_position_pct` |
| 2 | The portfolio flipped from `held` to `flat` under the same Sell proposal | `sell_requires_position` |
| 3 | A news claim cites a macro fact that does not exist (`macro_gdp`) | `claim_cites_known_fact` |
| 4 | A sentiment signal cites a post that does not exist (`S99`) | `sentiment_signal_cites_known_post` |
| 5 | A sentiment score falls outside the band it names | `sentiment_score_matches_band` |
| 6 | A lesson's text contains numbers | `lesson_text_has_no_numbers` |

Its two valid variants — a Buy with headroom under a held small position, and a score exactly on a band's upper edge — were not flagged.

**Scenarios.** Each file in `data/scenarios/` is a scripted run on the real stages, with its expected outcome stated in the file. The 21 from versions 2.0 and 2.1 cover every route through the flow (`clean`, `demo`, `misquote`, `stubborn`, `flailing`, `revise_levels`, `revise_thesis`, `contested`, `malformed_judge`, `malformed_manager`, `budget_early`, `budget_late`, `hold`, `thin`, `stale`, `unknown`, `channels_clean`, `misquote_fundamentals`, `ghost_item`, `uncovered`, `quiet`). The 13 new ones cover the capabilities:

| Scenario | Shows | Ends |
|---|---|---|
| `portfolio_flat_sell` | A Sell with a flat portfolio | review, `sell_requires_position` |
| `portfolio_held_sell` | A Sell with a held position | delivered |
| `portfolio_low_cash` | A Buy over the available cash, routed to the trader, revised | delivered |
| `risk_shrinks_size` | The conservative view shrinks the size; the trader revises | delivered |
| `risk_off_same` | `risk_rounds = 0` gives the board of `clean` | delivered, accepted 2/3 |
| `tools_clean` | One tool call; a claim cites `sma_30`, which the ledger does not hold | delivered |
| `tools_out_of_range` | An `n` outside the family's range; the error string goes back; the analyst answers without it | delivered |
| `news_clean` | News and macro available; twelve macro facts; both paths agree | delivered |
| `news_uncovered` | No news archive for the ticker: `unavailable` with the reason, the run continues | delivered |
| `sentiment_clean` | Posts in the window; the score inside its band | delivered |
| `macro_clean` | Macro on, no markets archive for the universe: macro facts, no markets key | delivered |
| `memory_lessons` | One lesson known before the date, one after: only the first reaches the board | delivered |
| `memory_record` | A delivered run appends one decision; a review run appends none | delivered / review |

The faults in the scenarios are planted by the script, so the catch rate is true by construction. What the scenarios show is the routing: which gate catches which fault, who is sent back, and what is delivered.

**Point in time, six new sweeps.** News and social (9 points each): an item dated after the analysis date never passes a port, and later items do not change an earlier window. Macro (91 points): a vintage dated after the analysis date is never used; two CPI restatements read day by day — 329.4 becomes 329.5 only from the day the revision takes effect, 337.0 becomes 337.2 likewise. Prediction markets (6 points): a snapshot is read only when `fetched_on` equals the analysis date. Lessons (5 points): an outcome known after the analysis date is never selected, the boundary day included. Tools (63 points): tools use only bars up to the analysis date — a deliberately leaky port that ignores `as_of` gives the same answers as the honest one on every family, date and window.

## 3. Real-model runs

**Setup.** Quick tier `deepseek:deepseek-flash` (five roles and the three judges, at low reasoning effort), deep tier `deepseek:deepseek-v4-pro` (the Research Manager, at high effort). Tool output (`run.output_mode = "auto"`). Prices from Pydantic AI's table. The synthetic tickers are `ACME` (US) and `0000.HK` and `GAPPY` (Hong Kong); `AAPL` and `0700.HK` are real bars from Eastmoney.

Every row below is a directory in `insightcrew/evals/archive/` holding the run's `journal.jsonl`. Revisions are `route` events. Retries are corrective retries (requests minus calls). Cost is in USD.

**Final v2.0 configuration, synthetic bars:**

| Run | Outcome | Rating | Revisions | Calls | Retries | Tokens | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|
| `20261119-093057-ACME-2026-09-30-f6aa` | delivered | Hold | 1 | 11 | 0 | 72,772 | 0.0717 | 228 |
| `20261119-093057-ACME-2026-05-29-6147` | delivered | Hold | 0 | 8 | 0 | 41,471 | 0.0289 | 96 |
| `20261119-093057-0000.HK-2026-09-30-9ab5` | delivered | Underweight | 0 | 8 | 0 | 45,973 | 0.0340 | 110 |
| `20261119-093057-0000.HK-2026-04-30-eb97` | delivered | Hold | 0 | 7 | 0 | 39,566 | 0.0316 | 101 |
| `20261119-093057-GAPPY-2026-09-29-d50e` | delivered | Hold | 0 | 8 | 0 | 45,132 | 0.0352 | 106 |

Five of five delivered, with no truncated reply and no corrective retry. A typical run takes about 100 seconds and 0.03 USD. The one revision came from the judges.

**Real bars,** analysis date 2026-11-18, which is after both models' stated training cutoffs:

| Run | Outcome | Rating | Revisions | Calls | Retries | Tokens | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|
| `20261119-093532-AAPL-2026-11-18-a161` | review, contested | (Overweight, withheld) | 1 | 12 | 1 | 88,319 | 0.0901 | 276 |
| `20261119-093533-0700.HK-2026-11-18-fdc0` | delivered | Underweight | 0 | 7 | 0 | 37,424 | 0.0322 | 96 |

The AAPL run is a refusal, so the report shows no rating. The rating in brackets is the last thesis in the journal. Section 6 walks through why it was refused.

## 3b. The channels on a real model (version 2.1)

Version 2.1 added two optional evidence channels. One pilot run (ACME 2026-09-30, both channels) showed both new analysts producing clean output — every fundamentals value copied exactly including the restated figures, well-reasoned disclosure signals, zero corrective retries, no audit findings — so no prompt was changed. The judges' dissents in that run were substantive (an unhandled price-to-earnings of about 130× as contrary evidence; a stop too close to the 20-day mean), which is the disagreement the debate exists for.

`uv run insightcrew eval channels` then ran 4 cells × 3 channel configurations. Per-cell run IDs are in `insightcrew/evals/archive/channels.json`; all twelve journals replay as 一致. The thirty version-2.0 journals above were made **before** the prompts of bull, bear, manager and judge gained their channels paragraph; these twelve, after.

| Configuration | Runs | Delivered | REVIEW rate | Revisions per run | Calls per run | Tokens per run | Cost per run | Thesis points citing channel evidence | Findings by check |
|---|---|---|---|---|---|---|---|---|---|
| none | 4 | 3 | 25% | 0.25 | 8.25 | 53,207 | 0.0198 | 0/14 | none |
| fundamentals | 4 | 3 | 25% | 0.75 | 11.0 | 75,336 | 0.0332 | 1/11 | none |
| both | 4 | 3 | 25% | 1.0 | 12.5 | 96,328 | 0.0482 | 3/14 | `point_cites_claimed_fact` × 2 |

What this shows, and what it does not:

- **The mechanism works against a real model.** The ACME cells read the channels as available, the analysts' output passed the audit, thesis points did cite channel evidence, and the two `point_cites_claimed_fact` findings in the `both` row show the widened audit catching a real mis-citation and routing it back.
- **The real-universe cells ran with both channels unavailable** — the live evidence downloads were blocked by the network during the build (ROADMAP), so no evidence files exist for real tickers and the channels report unavailable there. Those cells therefore differ from `none` only by the two status lines.
- **Cost and rework rise with the channels** (about 2.4× cost in `both`); the citation share stays low; the REVIEW rate is unchanged. Four cells per configuration cannot separate any of that from chance (section 7), which is why `[flow] channels` is left unset in `insightcrew.toml`.

## 3c. The version 2.2 cells: defined, not run

Version 2.2's five capabilities and the LangGraph orchestration have run only under the scripted model (the 13 scenarios above, 825 tests). `uv run insightcrew eval v22` lists the nine real-model cells and exits 2 — **they are not run**: each spends real money, and the owner's spending cap (HANDOFF question 14) has not been given.

| Cell | Request | Measures |
|---|---|---|
| `acme_all_on` | ACME 2026-09-30, every capability on, parallel | One run through all five capabilities and the parallel analysts |
| `acme_all_off` | ACME 2026-09-30, everything off | The baseline: behaviour identical to 2.1 |
| `acme_risk_on` | ACME, `risk_rounds = 1` | The risk debate alone |
| `acme_tools_on` | ACME, tools on | Analyst tool calls alone |
| `acme_portfolio` | ACME, `data/portfolios/flat_usd.json` | The portfolio rules and report section |
| `aapl_risk_on` | AAPL 2026-09-30, real bars, risk on | The debate on a real instrument |
| `aapl_tools_on` | AAPL, real bars, tools on | Tools on a real instrument |
| `hk_all_on` | 0700.HK 2026-09-30, real bars, everything on | The whole system on a Hong Kong instrument |
| `wall_serial` | ACME, `parallel = false` | Serial wall time, against the parallel cells |

The ablation this version was designed to inform — whether the risk debate and the three judges both earn their cost (BLUEPRINT decision 45) — is likewise **pending**: it waits on these cells. Nothing about the capabilities' effect on decisions, cost or wall time is claimed until then.

## 4. What the real runs changed

The design was tested offline before any real model ran. Four problems appeared only with a real model. Each was found by reading a journal and fixed one at a time:

| Run | What happened | Fix |
|---|---|---|
| `20261119-091407-ACME-2026-09-30-15ce` | The manager spent all 2,000 output tokens reasoning and never answered (`finish_reason: length`). The run ended `malformed_output`. The bear had used 1,809 of its 2,000 | Both DeepSeek models reason by default, and reasoning tokens count toward the output ceiling. Raised to 8,000 for the quick roles and 12,000 for the manager |
| `20261119-091600-ACME-2026-09-30-35da` | Two judges answered with text imitating the `<artifact>` blocks in their input instead of calling the output tool. The corrective retry fixed each, at the cost of a call | One line in `roles/prompts/_common.md`: submit through the output tool when one is given, and do not imitate the input format |
| Round 1 (below) | The evidence judge rejected 8 times out of 8. In 3 of those calls it went back and forth between accepting and rejecting ("Hmm, one more consideration…") until the ceiling cut it off | Judges run at low reasoning effort |
| Round 1 | The largest run used 107,145 tokens; the slowest took 338 seconds | `budget.run_tokens = 220000`; `limits.max_seconds = 900` |

Round 1, the same cells with the judges at high effort:

| Run | Outcome | Revisions | Calls | Retries | Truncated | Tokens | Cost | Seconds |
|---|---|---|---|---|---|---|---|---|
| `…091600-ACME-2026-09-30-35da` | review, contested | 1 | 12 | 2 | 1 | 101,264 | 0.0874 | 308 |
| `…092432-ACME-2026-09-30-3d67` | review, contested | 1 | 12 | 1 | 2 | 107,145 | 0.0912 | 338 |
| `…092432-ACME-2026-05-29-298c` | review, contested | 1 | 11 | 0 | 0 | 75,903 | 0.0725 | 296 |
| `…092432-0000.HK-2026-09-30-d6ee` | delivered | 0 | 8 | 0 | 0 | 49,275 | 0.0459 | 137 |
| `…092432-0000.HK-2026-04-30-74de` | delivered | 0 | 8 | 0 | 1 | 52,410 | 0.0417 | 138 |
| `…092432-GAPPY-2026-09-29-ee52` | delivered | 0 | 8 | 0 | 0 | 48,459 | 0.0394 | 129 |

Compared with the final configuration, refusals fell from 3 in 6 to 0 in 5, truncated replies from 4 to 0, and the typical cost roughly halved. The prompt line and the lower effort were changed in the same step, so their effects cannot be separated. Whether lower effort makes the judges' critiques worse was not measured. After the change their critiques still cite specific facts, but nobody scored them.

## 5. Ablation

`uv run insightcrew eval ablation` runs 4 cells × 4 configurations with a real model (version 2.0's configuration; the journals are archived). The cells are ACME on 2026-09-30 and 2026-05-29, and 0000.HK on 2026-09-30 and 2026-04-30. Each of the 16 run IDs is listed in `insightcrew/evals/archive/ablation.json`.

| Configuration | What is removed |
|---|---|
| `full` | Nothing |
| `no_judges` | The three judges |
| `shadow` | The judges; the audit still runs and records, but sends nothing back |
| `bare` | As `shadow`, and no bull/bear debate |

| Configuration | Delivered | Delivered that the audit would reject | Revisions per run | Retries per run | Tokens per run | Cost per run |
|---|---|---|---|---|---|---|
| `full` | 4/4 | 0/4 | 0.25 | 0.25 | 54,041 | 0.0459 |
| `no_judges` | 4/4 | 0/4 | 0 | 0 | 21,371 | 0.0205 |
| `shadow` | 4/4 | 0/4 | 0 | 0 | 24,507 | 0.0315 |
| `bare` | 4/4 | 0/4 | 0 | 0 | 15,114 | 0.0164 |

Ratings per cell:

| Cell | `full` | `no_judges` | `shadow` | `bare` |
|---|---|---|---|---|
| ACME 2026-09-30 | Hold | Overweight | Overweight | Buy |
| ACME 2026-05-29 | Hold | Hold | Hold | Buy |
| 0000.HK 2026-09-30 | Underweight | Underweight | Sell | Underweight |
| 0000.HK 2026-04-30 | Hold | Hold | Hold | Hold |

What this supports:

- **The judges are most of the cost.** They take a run from about 21,000 tokens to about 54,000, and the cost from about 0.02 to about 0.05 USD.
- **They changed one outcome in four cells.** On ACME 2026-09-30 they rejected an Overweight thesis, and the revision came back as Hold. Without judges the same cell was delivered as Overweight.
- **The audit found nothing to send back.** See section 6.
- **Run-to-run noise is about the size of the effects.** With an audit that never fires, `no_judges` and `shadow` are the same flow, yet they disagree on one cell in four (Underweight against Sell). Removing the debate changed two cells, both to Buy. That is barely above the noise, so it is evidence neither for nor against the debate.

The version 2.2 ablation — the risk debate against the judges, both lenses now arguing risk — is pending the cells of section 3c.

## 6. Failure cases and observations

**The audit never fired.** In every one of the 29 real-model runs that reached the audit (13 in sections 3 and 4, 16 in the ablation), the first proposal passed. That includes the `shadow` runs, where a failing proposal would have been delivered and counted. With this model, on these cells, the audit's measured effect is zero. Its value is shown by the fault injection and the scenarios, which prove that a faulty number or proposal cannot get through. A model that happens to be careful does not make the guarantee less necessary.

**A thesis pushed between two judges.** The AAPL run (`20261119-093532-AAPL-2026-11-18-a161`) shows the judges working, and also their limit:

1. The manager rated AAPL `Hold`. The evidence judge rejected the thesis: one point called the 20- and 50-day averages "nearby support", which argues for a favourable risk-to-reward, the opposite of the point's conclusion. The aggressive judge rejected it too: the cited readings (price above all three averages, a 60-day return of +13.7%, RSI 56.6) point to trend continuation, not waiting. The conservative judge accepted.
2. The thesis went back to the manager, which returned `Overweight` with a Buy at 352.45, stop 340, size 18%. The audit passed it.
3. The evidence judge rejected again. The favourable risk-to-reward it had pointed out holds only with a stop near the 20-day average, which would be 0.62 × ATR, below the 1 × ATR minimum. With the stop actually proposed, the downside (12.45) exceeds the upside to the 252-day high (8.38). The conservative judge rejected the levels.
4. With the round cap reached, the run ended `review`, `contested`, and no rating was shown.

Judges with opposed lenses can push a thesis back and forth; the round cap ends that in a refusal rather than letting the last push win. The evidence judge's second critique is a correct, specific piece of arithmetic that no code check makes: the audit checks each rule, not whether the thesis's argument agrees with the levels the trader chose.

**Reasoning that runs out of room.** The first run's failure (section 4) was invisible offline: a scripted model does not reason. A model that reasons by default needs output ceilings set from its real usage, and the ceiling is a setting in `roles/__init__.py`, not a guess.

**Replies that imitate the input.** The roles see their inputs as `<artifact>` blocks. Two judges answered with an imitation of that format rather than through the output tool. The corrective retry recovered both, which is what it is for, but each cost a call. A one-line prompt fix removed the problem in the later runs.

**The data source can disappear.** Yahoo answered HTTP 429 to every request from the development machine, and Stooq now serves a JavaScript check. The Eastmoney fallback was added for this. Either endpoint is unofficial and can change. The five version 2.2 sources have not been tried live at all.

**A crash loses only the interrupted call.** The checkpointed graph commits every node at its end, so the journal after a crash holds exactly the finished nodes and `resume` continues from there; the spend of a call interrupted mid-way is in no journal (a scripted interruption-and-resume test checks that the final board equals an uninterrupted run's). This is verified offline; no real-model interruption has been tried.

## 7. What these results do not show

- **Profitability or decision quality.** Tens of runs cannot separate skill from chance, and no rating was compared with what happened next.
- **Anything about the version 2.2 capabilities on a real model.** The nine cells are defined and not run (section 3c). Nothing is claimed about what the portfolio, the risk debate, tools, news, macro, sentiment or memory do to decisions, cost or wall time.
- **Anything about decisions from synthetic bars.** Most runs use generated prices, which carry no information. They test the mechanism.
- **Freedom from hindsight.** The point-in-time rule controls what the tools return. It does not control what a model remembers from training. The models state their own training cutoffs as 2024-06 (`deepseek-flash`) and 2023-10 (`deepseek-v4-pro`); these statements are unverified. The real-bar runs are dated 2026-11-18, after both.
- **Prompt quality from offline runs.** A scripted model ignores the prompt; the offline results say nothing about the prompts, including the six new ones of version 2.2.
- **That the judges verify.** L4 is scrutiny, not proof. The three judges share a model and can share a blind spot.
- **Anything about other models.** Every real-model result is from one DeepSeek pair.
- **The value of the debate.** The v2.0 ablation's effect is within the noise (section 5); the risk debate has not been ablated at all (section 3c).
- **That parallel analysts save a given amount.** Wall time is measured in memory and the `wall_serial` cell is not run; no number exists.
- **That headlines, posts or market probabilities are verified.** Code checks that an item exists and when it was published or fetched. What it says is judgment; market probabilities are quoted as the source's wording, unverified.
- **That sentiment from StockTwits or Reddit represents the market,** or that an archive started today covers anything earlier.
- **That a backtest on a handful of dates says anything about returns.** The backtest command is a mechanism (settle before analysing, no future lessons), demonstrated on a three-date scripted grid.
- **That more evidence gives better decisions.** Twelve v2.1 runs cannot show it (section 3b).
- **The channels on real evidence.** The live downloads were blocked by the network; the real-ticker cells ran with the channels unavailable (section 3b).

## 8. Reproducing

| What | Command | Needs |
|---|---|---|
| Offline evaluations | `uv run insightcrew eval all` | Nothing; exits 0 |
| Scenarios | `uv run insightcrew scenarios` | Nothing; 34 lines ✓ 相符 |
| The version 2.2 cells | `uv run insightcrew eval v22` | Nothing; lists the 9 cells and exits 2 until the owner caps spending |
| Tests and checks | `uv run pytest -q`, `uv run ruff check .`, `uv run pyright` | Nothing; 825 passed |
| Replay every archived journal | `for d in insightcrew/evals/archive/*/; do IC_FORCE_OFFLINE=1 uv run insightcrew replay "$d"; done` | Nothing for synthetic tickers; the two real-bar runs need `uv run insightcrew fetch AAPL 0700.HK --years 3` first, because downloaded bars are not committed |
| Interrupt and resume | Stop a scripted `analyze --scenario …` mid-run, then `uv run insightcrew resume runs/<id>` | Nothing (scripted); done in `tests/test_graph.py` |
| A scripted backtest | `tests/test_backtest.py` (three-date grid, resume with the same `--run-id`) | Nothing |
| One real-model run | `uv run insightcrew analyze ACME --date 2026-09-30`, or `uv run insightcrew analyze AAPL --universe real` after `fetch` | `DEEPSEEK_API_KEY` in `.env`; about 0.03 USD in the v2.0 configuration; the v2.2 capabilities cost more and are unmeasured |
| The ablation | `uv run insightcrew eval ablation` | The key; about 0.5 USD and several minutes |
| The channels evaluation | `uv run insightcrew eval channels` | The key; about 0.5 USD and several minutes |
| One run with channels | `uv run insightcrew analyze ACME --channels fundamentals,disclosures` | The key; about 0.05 USD |
| One run with a capability | `uv run insightcrew analyze ACME --risk-rounds 1` (or `--tools`, `--memory`, `--portfolio data/portfolios/held_acme.json`) | The key; unmeasured |

A re-run with a real model will not reproduce the same ratings: the model is not deterministic, which is the noise section 5 describes. Replaying an archived journal reproduces the recorded run exactly.
