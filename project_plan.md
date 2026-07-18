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

## Phase 6 — Task Queue + Minimal Interface ("Week 7")

RabbitMQ/Celery setup is pure infra and could be stood up any time earlier if you have downtime — it doesn't touch any of the modeling work.

**Day 1 — RabbitMQ + Celery** *[standalone — no data dependency]*
> Prompt Copilot: "Add RabbitMQ to docker-compose.yml (rabbitmq:3-management image, web UI at localhost:15672). Set up Celery from scratch to connect to it as a broker. Test with a trivial task first."

**Day 2 — Deferred job execution** *[depends on: Phase 2 calculator, Phase 5 schedule endpoint]*
> Prompt Copilot: "Define `execute_deferred_job(job_id, job_params, target_region, scheduled_time)` as a Celery task using `apply_async(eta=scheduled_time)`. It should run the calculator for the target region, fetch actual CI at execution time, compute actual vs. predicted carbon, and log the outcome."

**Day 3 — Outcome logging + monitoring**
> Prompt Copilot: "Create the `job_outcomes` table (job_id, predicted/actual region, ci, carbon, saving vs. immediate execution, saving vs. worst region) and wire it into the Celery task's logging step. Add Celery Flower as a new Docker service for live queue monitoring (localhost:5555). Add `GET /job/{job_id}` returning job status."

**Day 4–5 — Minimal frontend** *[depends on: Phase 5's /schedule, this phase's /job endpoint]*
> Prompt Copilot: "Build a minimal Next.js frontend: a job submission form (cores, memory, runtime, flexibility window, urgency toggle), a 'Get Recommendation' button calling `POST /schedule`, a results panel, and a job status view polling `GET /job/{job_id}` showing queued → scheduled → executing → complete, with actual vs. predicted carbon cost once a job finishes."

**End of Phase 6 deliverables**
- [ ] RabbitMQ + Celery running and tested
- [ ] `execute_deferred_job` fully implemented with ETA scheduling
- [ ] `job_outcomes` table logging predicted vs. actual
- [ ] Celery Flower running; `GET /job/{job_id}` working
- [ ] Minimal frontend: submission form, recommendation panel, job status view
- [ ] End-to-end test passed 5 times: submit → queue → fire at ETA → log outcome → visible in UI

---

## Phase 7 — Evaluation ("Week 8"; no paper writing)

This has to come last — it needs every real component (trained models, working scheduler, working queue) in place.

**Day 1 — Forecasting accuracy, final numbers**
> Prompt Copilot: "Re-run the evaluation harness on the final ARIMA, Prophet, and LSTM models across all 5 regions on the held-out test window. Produce a clean final results table (MAE, RMSE, MAPE per model per region)."

**Day 2 — Scheduling effectiveness**
> Prompt Copilot: "Define a job trace of ~50 synthetic jobs (varied cores/memory/runtime/region). Run two conditions: baseline (every job runs immediately in its submitted region) vs. treatment (every job goes through the scheduler). Compare total carbon cost using the real CEA emission factors. Compute % reduction and average saving per job."

**Day 3 — Ablation study**
> Prompt Copilot: "On the same 50-job trace, compare 3 scheduler variants: spatial-only (best region, run immediately), temporal-only (same region, best time), and full spatiotemporal (best region and time). Isolate how much each dimension contributes to total savings."

**Day 4 — System hardening**
> Prompt Copilot: "Add timeouts to every endpoint and graceful failure handling for Celery task failures. Run a final pass checking each endpoint fails cleanly under bad input."

**Day 5 — Demo + sanity check**
> Prompt Copilot: "Help me write a small `demo.py` script that submits a handful of test jobs and prints the before/after carbon comparison — this should be reliable for a live demo independent of real-time grid conditions."
Record a short demo video of the full flow. Sanity-check that the three result sets line up (e.g., does higher forecast error correlate with lower scheduling savings in the same region?).

**End of Phase 7 deliverables**
- [ ] Final forecasting accuracy table: ARIMA vs. Prophet vs. LSTM, all 5 regions
- [ ] Scheduling effectiveness result: % carbon reduction vs. baseline on the 50-job trace
- [ ] Ablation study: spatial-only vs. temporal-only vs. full spatiotemporal
- [ ] System hardened, demo video recorded
- [ ] **Paper writing is a separate, later phase — not part of this plan.**

---

## Working with Copilot (Opus 4.8) in VS Code, solo

- Open only the files relevant to the current day's task before prompting — Opus 4.8 does better with a focused context than the whole repo dumped in.
- Review every diff before accepting. Given you're newer to this Python/Windows tooling stack, read what changed even when it looks right — that's how you catch a wrong assumption (e.g., Copilot inventing a `coal_pct` column that shouldn't exist) before it compounds three phases later.
- Commit after each completed day-task, not at the end of a phase — makes it easy to roll back one bad Copilot suggestion without losing the rest.
- When a prompt above says "[depends on: …]", paste that dependency's actual file/table name into the Copilot prompt for context, not just the abstract description.