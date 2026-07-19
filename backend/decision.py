"""
backend/decision.py

Decision-engine scoring logic, implemented as a plain, dependency-free pure
function so it can be unit-tested in isolation before it is wired to an API
route (Phase 5, Day 5: ``POST /schedule``).

The engine answers a single question for a submitted job:

    "How much carbon can we save by shifting *when* and/or *where* this job
     runs, given how urgent it is and how far it is allowed to move?"

Scoring formula (project plan, Phase 5 Day 3):

    score = (CI_now - min(CI_in_window)) * (1 - urgency_weight)
          + (CI_current_region - CI_best_region) * spatial_weight

where
    CI_now             = carbon intensity in the job's region right now
    min(CI_in_window)  = greenest carbon intensity forecast for the job's
                         region within its flexibility window
    CI_current_region  = current carbon intensity of the job's region
    CI_best_region     = current carbon intensity of the greenest region
    urgency_weight     = 0..1; 1 == "must run now" (temporal shifting disabled)
    spatial_weight     = >= 0; how strongly to value moving the job elsewhere

Both terms are non-negative by construction (``CI_now`` is the first element of
the window, and the current region is one of the candidate regions), so a
higher ``score`` means a larger achievable carbon saving.

The scoring function (:func:`score_scheduling`) is pure and has no database or
framework dependency. A thin persistence helper (:func:`record_decision`) is
provided so callers (e.g. the ``POST /schedule`` route) can log every decision
the engine makes to the ``scheduling_decisions`` table; it is deliberately kept
separate from the scoring logic so the maths can still be unit-tested in
isolation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

if TYPE_CHECKING:  # avoid importing SQLAlchemy at module import time for the pure path
    from sqlalchemy.orm import Session

    from backend.db.models import SchedulingDecision



@dataclass(frozen=True)
class SchedulingScore:
    """Result of scoring a single job.

    Attributes:
        score: Total achievable saving signal (gCO2e/kWh), weighted by urgency
            (temporal term) and spatial preference (spatial term).
        temporal_saving: Unweighted CI drop from running at the greenest time
            in the window instead of now (``CI_now - min(CI_in_window)``).
        spatial_saving: Unweighted CI drop from running in the greenest region
            instead of the current one (``CI_current - CI_best_region``).
        best_time_index: Index into ``ci_window`` of the greenest slot
            (0 == run now).
        best_region: Name of the greenest region (may equal ``current_region``).
        recommend_shift_time: True if a strictly greener future slot exists.
        recommend_shift_region: True if a strictly greener region exists.
    """

    score: float
    temporal_saving: float
    spatial_saving: float
    best_time_index: int
    best_region: str
    recommend_shift_time: bool
    recommend_shift_region: bool


def score_scheduling(
    *,
    current_region: str,
    region_ci_now: Mapping[str, float],
    ci_window: Sequence[float],
    urgency_weight: float,
    spatial_weight: float = 1.0,
) -> SchedulingScore:
    """Score the carbon-saving potential of shifting a job in time and/or space.

    Args:
        current_region: The region the job would run in by default. Must be a
            key of ``region_ci_now``.
        region_ci_now: Current carbon intensity (gCO2e/kWh) per region. Used to
            find the greenest region for the spatial term. Must be non-empty.
        ci_window: Forecast carbon intensity (gCO2e/kWh) for ``current_region``
            across the job's flexibility window, ordered by time. ``ci_window[0]``
            is "now". A single-element window means zero flexibility. Must be
            non-empty.
        urgency_weight: 0..1. At 1 the job is fully urgent and the temporal
            term is zeroed (it cannot wait). At 0 the temporal saving counts
            in full.
        spatial_weight: >= 0. Weight applied to the region-shift saving.

    Returns:
        A :class:`SchedulingScore` describing the total score, its temporal and
        spatial components, and the recommended time slot / region.

    Raises:
        ValueError: If ``ci_window`` or ``region_ci_now`` is empty, if
            ``current_region`` is not present in ``region_ci_now``, if any CI
            value is negative, if ``urgency_weight`` is outside [0, 1], or if
            ``spatial_weight`` is negative.
    """
    if not ci_window:
        raise ValueError("ci_window must contain at least one value (now).")
    if not region_ci_now:
        raise ValueError("region_ci_now must contain at least one region.")
    if current_region not in region_ci_now:
        raise ValueError(
            f"current_region {current_region!r} not found in region_ci_now."
        )
    if not 0.0 <= urgency_weight <= 1.0:
        raise ValueError("urgency_weight must be between 0 and 1 inclusive.")
    if spatial_weight < 0.0:
        raise ValueError("spatial_weight must be non-negative.")
    if any(ci < 0.0 for ci in ci_window):
        raise ValueError("ci_window values must be non-negative.")
    if any(ci < 0.0 for ci in region_ci_now.values()):
        raise ValueError("region_ci_now values must be non-negative.")

    # --- Temporal term: run at the greenest slot within the window. ---
    # KNOWN LIMITATION (model choice, not a bug): the ci_window is produced by
    # the per-region ARIMA forecast, whose multi-day rollout converges to a
    # near-flat value within ~2-3 days (e.g. NR sits at ~523.232 gCO2e/kWh from
    # day 3 of a 60-day forecast onward). With an almost-constant window,
    # ci_now ~= min_ci_in_window, so temporal_saving is ~0 and this temporal
    # term rarely (in practice never) recommends a beneficial time shift. The
    # spatial term below is therefore doing essentially all of the useful work
    # today. This is an explainable consequence of plain ARIMA at long horizons
    # and should be treated as such in the Phase 7 evaluation (a seasonal model
    # would be needed to revive a meaningful temporal signal).
    ci_now = ci_window[0]
    best_time_index = min(range(len(ci_window)), key=ci_window.__getitem__)
    min_ci_in_window = ci_window[best_time_index]
    temporal_saving = ci_now - min_ci_in_window

    # --- Spatial term: run in the greenest region right now. ---
    ci_current_region = region_ci_now[current_region]
    # Deterministic tie-break: keep the current region when it is already
    # (one of) the greenest, otherwise pick the greenest by region name.
    best_region = min(
        region_ci_now,
        key=lambda r: (region_ci_now[r], r != current_region, r),
    )
    ci_best_region = region_ci_now[best_region]
    spatial_saving = ci_current_region - ci_best_region

    score = temporal_saving * (1.0 - urgency_weight) + spatial_saving * spatial_weight

    return SchedulingScore(
        score=score,
        temporal_saving=temporal_saving,
        spatial_saving=spatial_saving,
        best_time_index=best_time_index,
        best_region=best_region,
        recommend_shift_time=best_time_index != 0 and temporal_saving > 0.0,
        recommend_shift_region=best_region != current_region and spatial_saving > 0.0,
    )


def record_decision(
    session: "Session",
    *,
    job_id: str,
    default_region: str,
    recommended_region: str,
    predicted_saving_gco2e: float,
    urgency_weight: float,
    recommended_time: Optional[datetime] = None,
    submitted_at: Optional[datetime] = None,
) -> "SchedulingDecision":
    """Persist one decision-engine recommendation to ``scheduling_decisions``.

    This is the "wire the engine to log every decision" side of the decision
    engine. It is intentionally decoupled from :func:`score_scheduling`: the
    caller decides *what* to store (having already converted the CI-based score
    into a concrete gCO2e saving and resolved the recommended timestamp), and
    this helper just writes and commits the row.

    Args:
        session: An active SQLAlchemy session.
        job_id: Caller-supplied identifier for the job.
        default_region: Region the job would run in without scheduling.
        recommended_region: Region the engine recommends (may equal default).
        predicted_saving_gco2e: Estimated carbon saved by the recommendation
            (gCO2e). Must be a real, finite, non-negative number. It may only be
            exactly ``0`` when the recommendation is effectively "run now" in the
            default region (no region change and no deferred time); a genuine
            spatial or temporal shift must carry a strictly positive saving.
        urgency_weight: The 0..1 urgency weight used for this decision.
        recommended_time: Recommended execution time, or ``None`` for "run now".
        submitted_at: When the job was submitted. Defaults to the current UTC
            time if not supplied.

    Returns:
        The persisted :class:`~backend.db.models.SchedulingDecision` row
        (with its generated ``id`` populated).

    Raises:
        ValueError: If ``predicted_saving_gco2e`` is ``None``, not a finite
            number, negative, or exactly zero while the recommendation is a real
            shift; or if ``urgency_weight`` is outside [0, 1].
    """
    if predicted_saving_gco2e is None:
        raise ValueError("predicted_saving_gco2e must be provided (got None).")
    if not math.isfinite(predicted_saving_gco2e):
        raise ValueError(
            f"predicted_saving_gco2e must be a finite number "
            f"(got {predicted_saving_gco2e!r})."
        )
    if predicted_saving_gco2e < 0.0:
        raise ValueError("predicted_saving_gco2e must be non-negative.")
    if not 0.0 <= urgency_weight <= 1.0:
        raise ValueError("urgency_weight must be between 0 and 1 inclusive.")

    # A recommendation that changes the region or defers the time is a real
    # "shift" and must carry a positive saving; a zero saving there means the
    # caller passed a placeholder rather than a genuinely computed value. Zero is
    # only legitimate for a "run now in the default region" recommendation.
    is_shift = recommended_region != default_region or recommended_time is not None
    if is_shift and predicted_saving_gco2e == 0.0:
        raise ValueError(
            "predicted_saving_gco2e is 0 but the recommendation is a shift "
            f"({default_region} -> {recommended_region}, "
            f"time={recommended_time!r}); a real shift must have a positive "
            "saving, so this looks like a placeholder rather than a computed value."
        )

    # Imported here (not at module top) so the pure scoring path stays free of
    # any database dependency.
    from backend.db.models import SchedulingDecision

    decision = SchedulingDecision(
        job_id=job_id,
        submitted_at=submitted_at or datetime.now(timezone.utc),
        default_region=default_region,
        recommended_region=recommended_region,
        recommended_time=recommended_time,
        predicted_saving_gco2e=predicted_saving_gco2e,
        urgency_weight=urgency_weight,
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return decision

