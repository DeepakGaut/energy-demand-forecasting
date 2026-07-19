# EcoCompute — Remaining Build Plan (Solo + Copilot/Opus 4.8 in VS Code)

**Starting point:** Week 1 and Week 2 Day 1–3 are done (by all three of you). Everything from Week 2 Day 4 onward is merged into one linear track, resequenced by actual technical dependency instead of by original per-person ownership.
**Excluded:** Paper writing. That stays a separate, later phase — nothing below produces LaTeX or manuscript text.
**Tech stack:** Unchanged — FastAPI, PostgreSQL, Redis, Celery + RabbitMQ, Next.js/Tailwind/Recharts, Docker Compose, pandas/statsmodels/Prophet/PyTorch/MLflow.

## A quick, honest note on timeline

The original plan had 3 people moving simultaneously — while one person cleaned data, another was already building the FastAPI skeleton, another was setting up Docker. That parallelism is gone now that it's one person + Copilot working serially. Resequencing by dependency claws back *some* of that lost time — by front-loading tasks that don't need real data yet (Docker Compose, FastAPI skeleton, the carbon calculator, the scheduler's scoring logic, Celery plumbing) — but it can't fully replace three people working at once. Treat the "Week" labels below as **work-blocks**, not calendar guarantees. If you're doing this alongside classes, it will likely take noticeably longer in calendar time than the original Week 3–7 window, even though the total task list hasn't grown.

## How to read each task

Each task is written so you can paste it near-verbatim as a Copilot chat prompt in VS Code. Where a task depends on a real artifact from an earlier step (a trained model, a populated table), that's flagged **[depends on: …]**. Where a task is pure infrastructure or a pure function with no dependency on real data, it's flagged **[standalone — no data dependency]**, meaning if you ever have downtime waiting on something (a long training run, a slow scrape), you can pull one of these forward.

---

## Phase 1 — Finish the Data Foundation (rest of "Week 2")

This has to come first, non-negotiably — nothing downstream (forecasting, scheduling, evaluation) means anything if the CI numbers underneath are wrong.

**Day 1 — Resolve the schema question and finalize the dataset**
> Prompt Copilot: "Open `parser_all_regions.py`. I need to determine whether the coal/gas/nuclear columns it outputs per region are directly measured from the source PSP reports, or derived/estimated. Walk through the parsing logic with me line by line and tell me which columns come from raw report fields vs. any interpolation or allocation logic."

Based on that check: if the per-region thermal breakdown turns out to be modeled/estimated rather than measured, discard it and commit to the agreed **Option A schema**: `date, region, hydro_mwh, wind_mwh, solar_mwh, energy_met_mwh, other_mwh` (coal+gas+nuclear lumped, since India's grid has been unified nationally since 2013 and only renewables break out regionally).


> Prompt Copilot: "Update `clean_and_compute_ci.py` so the output strictly follows this schema: date, region, hydro_mwh, wind_mwh, solar_mwh, energy_met_mwh, other_mwh. Convert MU to MWh (× 1000). Compute CI as gCO2e/kWh using emission factors: Coal 0.969, Gas 0.452, ~0.961 for the lumped 'other' bucket, and 0 for hydro/wind/solar. Output both absolute MWh and each source's % share of energy_met_mwh per region-day."

**Day 2 — Reconcile the DB schema and build the real data loader** [depends on: Day 1 output]
Your `ci_timeseries` table currently expects percentage columns per fuel and gCO2e/kWh — but your CSV pipeline outputs MWh and tCO2/MWh, and only has hydro/wind/solar/other (no separate coal/gas/nuclear at regional level). Fix the mismatch at the loader, not by inventing regional coal/gas/nuclear numbers.

> Prompt Copilot: "Review my SQLAlchemy model for `ci_timeseries`. Its columns currently assume coal_pct, hydro_pct, wind_pct, solar_pct, nuclear_pct, gas_pct per region. Since regional coal/gas/nuclear data doesn't exist, alter the model and generate an Alembic migration so the table instead stores: hydro_pct, wind_pct, solar_pct, other_pct, and ci_gco2e_per_kwh. Then write `load_ci_data.py`: read the cleaned CSV, convert tCO2/MWh × generation to total tCO2, divide by total MWh × 1000 to get gCO2e/kWh, compute each source's % of energy_met_mwh, and insert into ci_timeseries."

> Prompt Copilot: "Run `load_ci_data.py` and verify row counts match the source CSV for all 5 regions. Print a sanity check: min/max/mean CI per region, and flag any region-day where percentages don't sum to ~100%."

**Day 3 — Docker Compose skeleton** *[standalone — no data dependency]*
> Prompt Copilot: "Create `docker-compose.yml` from scratch with 3 services: postgres (with a named volume so data persists), redis, and fastapi (build from a Dockerfile you also create). Wire environment variables for the DB connection string. Get all three healthy with `docker-compose up`."

**End of Phase 1 deliverables**
- [ ] Verified whether `parser_all_regions.py`'s per-region thermal split is real or modeled, and schema finalized accordingly
- [ ] `clean_and_compute_ci.py` outputs the agreed Option A schema with correct units
- [ ] `ci_timeseries` model/migration updated to match reality (no invented regional coal/gas/nuclear)
- [ ] `load_ci_data.py` written, run, and row counts verified against source CSVs
- [ ] `docker-compose.yml` running postgres + redis + fastapi healthy

---

## Phase 2 — Backend Serving Layer ("Week 3", first half)

All of this is standalone infra/logic work — none of it needs the forecasting models to exist yet.

**Day 1 — FastAPI skeleton + serve the real CI data** *[depends on: Phase 1 data being loaded]*
> Prompt Copilot: "Build the FastAPI app skeleton: project structure, a health-check endpoint at `/health`, and `GET /ci/{region}` that queries `ci_timeseries` and returns the last 30 days of CI as JSON, ordered by date."

**Day 2 — Carbon footprint calculator** *[standalone — no data dependency, pure function]*
> Prompt Copilot: "Implement the Green Algorithms formula as a plain Python function: C = t × [(Nc×Pc×Uc) + (Nm×Pm)] × PUE × CI × 10⁻³. Use the existing `hardware_specs` seed table for CPU/GPU TDP lookups. Write unit tests with a few hand-calculated example inputs so I can trust the math before wiring it to a route. Then expose it as `POST /calculate`, taking job specs (hardware, duration, region) and returning estimated CO2e, pulling live CI from `ci_timeseries` for that region."

**Day 3 — Redis caching layer** *[standalone]*
> Prompt Copilot: "Add Redis caching in front of `GET /ci/{region}` and `POST /calculate`. Use region+date as the cache key for the CI endpoint, and a hash of the job inputs + region for the calculator. Write a small before/after benchmark script measuring latency with and without cache hits."

**End of Phase 2 deliverables**
- [ ] `GET /ci/{region}` live against real data
- [ ] Green Algorithms calculator implemented, unit-tested, and exposed via `POST /calculate`
- [ ] Redis caching added with a measured latency improvement

---

## Phase 3 — Forecasting: Baselines ("Week 3" second half + "Week 4")

Now that real CI data is loaded, this can start.

**Day 1 — Feature set** *[depends on: Phase 1 data]*
> Prompt Copilot: "Build `features.py`: extract past-24h CI values, hour of day, day of week, month, and each region's generation-mix percentages (hydro/wind/solar/other) from `ci_timeseries` for use in forecasting models. This should output one clean DataFrame per region ready for both ARIMA and Prophet."

**Day 2–3 — ARIMA + Prophet, one region at a time**
> Prompt Copilot: "Train an ARIMA model (statsmodels) on the historical CI series for [region]. Tune (p,d,q) if the default fit is poor. Save the trained model to `models/arima/{region}.pkl`."
> Prompt Copilot: "Now train a Prophet model on the same series for [region], letting it capture daily/weekly/yearly seasonality natively. Save to `models/prophet/{region}.pkl`."
Repeat across all 5 regions (NR, SR, ER, WR, NER).

**Day 4 — Shared evaluation harness**
> Prompt Copilot: "Build `eval.py`: a fixed train/test split (final 2 months held out), with functions computing MAE, RMSE, and MAPE per region, so it can score any model without modification."

**Day 5 — Run it, compare**
> Prompt Copilot: "Run `eval.py` against both the ARIMA and Prophet forecasts for all 5 regions. Produce a results table (model × region × metric), and flag which regions are hardest to forecast and why."

**End of Phase 3 deliverables**
- [ ] `features.py` built from real data
- [ ] ARIMA + Prophet trained and saved for all 5 regions
- [ ] `eval.py` built and run
- [ ] Baseline results table: ARIMA vs Prophet across all 5 regions

---

## Phase 4 — Forecasting: LSTM + Serving ("Week 5")

**Day 1–2 — LSTM training + MLflow**
> Prompt Copilot: "Set up MLflow for experiment tracking locally. Then design and train an LSTM (PyTorch) per region using `features.py`'s output, aiming to capture daily solar cycles, weekly demand patterns, and monsoon-driven hydro variation. Log every run's hyperparameters and metrics to MLflow."

**Day 3 — Hyperparameter sweep**
> Prompt Copilot: "Run a small hyperparameter sweep on the LSTM — hidden size, sequence length, learning rate — tracked in MLflow, and pick the best config per region based on validation loss."

**Day 4 — Compare against baselines** *[depends on: Phase 3 results]*
> Prompt Copilot: "Run `eval.py` on the LSTM's forecasts for all 5 regions and produce a final comparison table against the Week [Phase 3] ARIMA/Prophet numbers. It's fine if the winner differs by region — note which model wins where."

**Day 5 — Serve the best model**
> Prompt Copilot: "Build `GET /forecast/{region}` that loads whichever model wins for that region (LSTM, ARIMA, or Prophet) and returns the next 24 hours of predicted CI as JSON. Add Redis caching with a 30-minute TTL. Write an integration test calling it for all 5 regions and checking the response shape."

**End of Phase 4 deliverables**
- [ ] LSTM trained and saved for all 5 regions, tracked in MLflow
- [ ] Final model-comparison table: ARIMA vs Prophet vs LSTM per region
- [ ] `GET /forecast/{region}` live, serving the best model per region, cached

---

## Phase 5 — Decision Engine + Scheduler API ("Week 6")

The scoring logic here is a pure function and technically could have been built earlier against mock CI/forecast values — flagging that in case you ever have idle time before Phase 4 finishes and want to pull it forward.

**Day 1 — Compare-regions endpoint** *[depends on: Phase 2 calculator, Phase 1 data]*
> Prompt Copilot: "Build `GET /compare-regions`: accepts job parameters, runs the Green Algorithms calculator across all 5 regions using their current CI, and returns a ranked list — region, ci, carbon_gco2e, % vs worst region."

**Day 2 — Multi-region forecast** *[depends on: Phase 4]*
> Prompt Copilot: "Add a `?compare=true` option to `GET /forecast/{region}` that returns 24h forecasts for all 5 regions in a single call, so the decision engine doesn't need 5 round trips."

**Day 3 — Scoring logic** *[standalone logic, but wire-up depends on above]*
> Prompt Copilot: "Implement the decision engine scoring function: score = (CI_now − min(CI_in_window)) × (1 − urgency_weight) + (CI_current_region − CI_best_region) × spatial_weight. Write unit tests covering edge cases: all regions equal, job marked urgent, zero flexibility window."

**Day 4 — Logging table**
> Prompt Copilot: "Create the `scheduling_decisions` table: job_id, submitted_at, default_region, recommended_region, recommended_time, predicted_saving_gco2e, urgency_weight. Wire the decision engine to log every decision it makes."

**Day 5 — POST /schedule + integration test**
> Prompt Copilot: "Build `POST /schedule`: accepts job specs + flexibility_window_hours + urgency_flag, internally calls `/compare-regions` and `/forecast` with `?compare=true`, passes both signals into the decision engine, and returns a full recommendation (region, time, predicted saving, confidence). Write an integration test: submit a flexible job and confirm it recommends a genuinely different, lower-carbon region or time when one exists."

**End of Phase 5 deliverables**
- [ ] `GET /compare-regions` returning a ranked 5-region list
- [ ] Decision engine scoring function with passing unit tests
- [ ] `scheduling_decisions` table logging every decision
- [ ] `POST /schedule` working end-to-end with a passing integration test

---

## Phase 6 — Frontend ("Week 7")

Decision: the task queue (RabbitMQ/Celery, deferred execution, job_outcomes, 
Flower) is dropped entirely. POST /schedule already returns a full 
recommendation synchronously — there's no "queued → executing → complete" 
lifecycle to build a UI around, since nothing runs asynchronously. This phase 
is now 100% frontend, built as a proper interface, not a minimal one.

Tech stack unchanged: Next.js + Tailwind + Recharts.

**New, small backend addition (flagging — confirm before building):**
> Prompt Copilot: "Add GET /decisions — returns the most recent N rows from 
> scheduling_decisions (job_id, submitted_at, default_region, 
> recommended_region, recommended_time, predicted_saving_gco2e), paginated or 
> limited to the latest 50 by default. This powers a decision-history view in 
> the frontend."

**Day 1 — Scaffolding + design system**
> Prompt Copilot: "Scaffold a Next.js + Tailwind app. Set up a clean design 
> system: color palette (tie it to carbon intensity — e.g. green→red gradient 
> for low→high CI), typography, a shared layout with navigation between pages 
> (Calculator, Schedule, Regions, History). Build a typed API client wrapper 
> for all backend endpoints (/calculate, /schedule, /compare-regions, 
> /forecast/compare, /decisions) so pages don't hand-roll fetch calls."

**Day 2 — Calculator + Schedule pages**
> Prompt Copilot: "Build the Calculator page: a form for hardware selection, 
> core count, memory, runtime, PUE — calls POST /calculate, shows the 
> resulting CO2e clearly (with the ci_source: measured/forecasted label 
> visible, not hidden). Build the Schedule page: job submission form (cores, 
> memory, runtime, flexibility window, urgency toggle) calling POST /schedule, 
> displaying the full recommendation — region, time (or 'run now'), predicted 
> saving, confidence — as a clear result card, not a raw JSON dump."

**Day 3 — Region comparison + forecast visualizations**
> Prompt Copilot: "Build a Regions page: a bar chart (Recharts) ranking all 5 
> regions by current CI, using GET /compare-regions for a given job spec. Add 
> a line chart showing each region's 60-day forecasted CI curve from 
> GET /forecast/compare?horizon=60, so a user can visually see which regions 
> trend greener over the coming weeks."

**Day 4 — Decision history dashboard**
> Prompt Copilot: "Build a History page reading from GET /decisions: a table 
> of past scheduling recommendations (date, default region, recommended 
> region, predicted saving), plus a summary stat card at the top (total 
> predicted savings across all logged decisions, most-recommended region)."

**Day 5 — Polish pass**
> Prompt Copilot: "Polish pass across all pages: responsive layout (mobile + 
> desktop), loading states for every API call, clear error states (e.g. 
> backend down, invalid input), empty states (e.g. no decisions logged yet). 
> Final visual review — consistent spacing, typography, and the CI 
> color-coding applied consistently across Calculator, Schedule, and Regions 
> pages. Prepare for Vercel deployment (env vars for backend URL)."

**End of Phase 6 deliverables**
- [ ] GET /decisions endpoint added and tested
- [ ] Calculator page — working, shows ci_source clearly
- [ ] Schedule page — working, clear recommendation display
- [ ] Regions page — bar chart (current CI) + line chart (60-day forecast)
- [ ] History dashboard — table + summary stats from real logged decisions
- [ ] Fully responsive, with loading/error/empty states handled
- [ ] Deployed to Vercel (or ready to deploy)

**Explicitly dropped from this phase:** RabbitMQ, Celery, execute_deferred_job, 
job_outcomes table, Celery Flower, GET /job/{job_id}. If deferred/async job 
execution becomes relevant later (e.g. for a live demo), it can be revisited 
as a separate addition — not part of this plan.

---

## Phase 7 — Evaluation ("Week 8"; no paper writing)

This has to come last — it needs every real component in place: trained models, 
working scheduler, working frontend. (Task queue dropped per Phase 6 decision — 
no Celery/RabbitMQ references below.)

**Day 1 — Forecasting accuracy, final numbers**
> Prompt Copilot: "Re-run the evaluation harness on the final ARIMA, Prophet, 
> and LSTM models across all 5 regions on the held-out test window. Produce a 
> clean final results table (MAE, RMSE, MAPE per model per region)."

**Day 2 — Scheduling effectiveness**
> Prompt Copilot: "Define a job trace of ~50 synthetic jobs (varied 
> cores/memory/runtime/region). Run two conditions: baseline (every job runs 
> immediately in its submitted region) vs. treatment (every job goes through 
> the scheduler). Compare total carbon cost using the real CEA emission 
> factors. Compute % reduction and average saving per job. Use the same 
> wall-clock-forecasted CI convention as the live API (Phase 5/6 update) so 
> this evaluation is consistent with what the deployed system actually 
> reports — not the old fixed-dataset-relative dates."

**Day 3 — Ablation study**
> Prompt Copilot: "On the same 50-job trace, compare 3 scheduler variants: 
> spatial-only (best region, run immediately), temporal-only (same region, 
> best time), and full spatiotemporal (best region and time). Isolate how much 
> each dimension contributes to total savings. For the 'full' condition, 
> compute the true saving directly as CI(default_region, now) − 
> CI(chosen_region, chosen_time) rather than summing the spatial and temporal 
> components separately — we've confirmed that additive sum overstates savings 
> when both a region and time shift happen together (documented in 
> POST /schedule's response model)."

> **KNOWN LIMITATION — temporal signal is near-zero with plain ARIMA (model choice, not a bug).**
> The per-region ARIMA forecast's multi-day rollout converges to a near-flat 
> value within ~2-3 days (verified live: NR sits at ~523.232 gCO2e/kWh from 
> day 3 of a 60-day forecast onward). Because the flexibility window is almost 
> constant, ci_now ≈ min(window), so the scheduler's **temporal term produces 
> ~0 saving and rarely/never recommends a time shift** — the **spatial** 
> (region-shift) term is doing essentially all the useful work today. Expect 
> the ablation to show: spatial-only ≈ full spatiotemporal, and temporal-only 
> ≈ baseline (~0% reduction). This is an explainable property of ARIMA at long 
> horizons, not a defect in the scoring logic (see the comment on 
> `score_scheduling` in `backend/decision.py`). Reviving a meaningful temporal 
> signal would require a seasonal model (e.g. SARIMA / Prophet with 
> seasonality); treat that as future work when interpreting Phase 7 results.

**Day 4 — System hardening**
> Prompt Copilot: "Add timeouts to every backend endpoint and graceful failure 
> handling for bad/missing inputs (unknown region, invalid hardware, malformed 
> job specs). Run a final pass confirming each endpoint fails cleanly with a 
> clear error message under bad input — no unhandled 500s. Also verify the 
> frontend handles backend downtime and slow responses gracefully (loading/ 
> error states from Phase 6's polish pass)."

**Day 5 — Demo + sanity check**
> Prompt Copilot: "Help me write a small `demo.py` script that submits a 
> handful of test jobs through the real API and prints the before/after 
> carbon comparison — reliable for a live demo regardless of what day it's 
> run, since the wall-clock forecast remapping means 'today' always has a 
> valid forecasted CI. Also do a full walkthrough of the actual frontend 
> (Calculator → Schedule → Regions → History) as the primary demo path, with 
> demo.py as a backend-only fallback."
Record a short demo video of the full flow through the frontend. Sanity-check 
that the three result sets line up (e.g., does higher forecast error correlate 
with lower scheduling savings in the same region?).

**End of Phase 7 deliverables**
- [ ] Final forecasting accuracy table: ARIMA vs. Prophet vs. LSTM, all 5 regions
- [ ] Scheduling effectiveness result: % carbon reduction vs. baseline on the 50-job trace
- [ ] Ablation study: spatial-only vs. temporal-only vs. full spatiotemporal (using the corrected, non-additive saving calculation for the "full" condition)
- [ ] System hardened (backend + frontend), demo video recorded through the actual UI
- [ ] **Paper writing is a separate, later phase — not part of this plan.**

---

## Working with Copilot (Opus 4.8) in VS Code, solo

- Open only the files relevant to the current day's task before prompting — 
  Opus 4.8 does better with a focused context than the whole repo dumped in.
- Review every diff before accepting. Read what changed even when it looks 
  right — that's how you catch a wrong assumption (e.g., a stale/hardcoded 
  date, or a schema mismatch) before it compounds phases later.
- Commit after each completed day-task, not at the end of a phase — makes it 
  easy to roll back one bad suggestion without losing the rest.
- When a prompt says "[depends on: …]", paste that dependency's actual 
  file/table name into the prompt for context, not just the abstract 
  description.
- When something looks surprisingly good (a suspiciously low error, a metric 
  that barely changed when it should have gotten harder), don't take it at 
  face value — ask for the underlying evidence (day-by-day values, not just 
  aggregates) before trusting it. This pattern caught real issues multiple 
  times already in this project.