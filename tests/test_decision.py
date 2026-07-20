"""Unit tests for the decision-engine scoring function (backend/decision.py).

Scoring formula (project plan, Phase 5 Day 3):

    score = (CI_now - min(CI_in_window)) * (1 - urgency_weight)
          + (CI_current_region - CI_best_region) * spatial_weight

All CI values below are hand-picked so every expected number can be verified
by inspection, keeping the test independent of any database or model.
"""

import pytest

from backend.decision import SchedulingScore, score_scheduling


def test_shift_both_time_and_region():
    # current region NR: now=500, greener slot at index 2 (min=440) -> temporal=60
    # regions now: NR=500 (current), SR=420 (greenest) -> spatial=80
    # urgency_weight=0.0 -> temporal counts in full; spatial_weight=1.0
    # score = 60 * 1.0 + 80 * 1.0 = 140
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 500.0, "SR": 420.0, "ER": 480.0},
        ci_window=[500.0, 470.0, 440.0, 460.0],
        urgency_weight=0.0,
        spatial_weight=1.0,
    )
    assert isinstance(result, SchedulingScore)
    assert result.temporal_saving == pytest.approx(60.0)
    assert result.spatial_saving == pytest.approx(80.0)
    assert result.score == pytest.approx(140.0)
    assert result.best_time_index == 2
    assert result.best_region == "SR"
    assert result.recommend_shift_time is True
    assert result.recommend_shift_region is True


def test_all_regions_equal_no_spatial_saving():
    # Every region has the same current CI -> spatial term is exactly 0,
    # and the greenest region stays the current one (no region shift).
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 450.0, "SR": 450.0, "ER": 450.0},
        ci_window=[450.0, 430.0],  # temporal saving still possible
        urgency_weight=0.0,
        spatial_weight=1.0,
    )
    assert result.spatial_saving == pytest.approx(0.0)
    assert result.best_region == "NR"
    assert result.recommend_shift_region is False
    # Only the temporal term contributes.
    assert result.temporal_saving == pytest.approx(20.0)
    assert result.score == pytest.approx(20.0)
    assert result.recommend_shift_time is True


def test_urgent_job_zeroes_temporal_term():
    # urgency_weight=1.0 -> job must run now -> temporal contribution = 0,
    # even though a greener future slot exists. Spatial saving still counts.
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 500.0, "SR": 400.0},
        ci_window=[500.0, 300.0],  # big temporal saving that must be ignored
        urgency_weight=1.0,
        spatial_weight=1.0,
    )
    # temporal_saving is still reported (raw signal) ...
    assert result.temporal_saving == pytest.approx(200.0)
    # ... but it contributes nothing to the score because (1 - 1.0) == 0.
    assert result.spatial_saving == pytest.approx(100.0)
    assert result.score == pytest.approx(100.0)


def test_zero_flexibility_window_no_temporal_saving():
    # A single-element window == zero flexibility: the only option is "now",
    # so min(window) == CI_now and temporal saving is 0.
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 500.0, "SR": 400.0},
        ci_window=[500.0],
        urgency_weight=0.0,
        spatial_weight=1.0,
    )
    assert result.best_time_index == 0
    assert result.temporal_saving == pytest.approx(0.0)
    assert result.recommend_shift_time is False
    # Spatial term is the only contribution.
    assert result.spatial_saving == pytest.approx(100.0)
    assert result.score == pytest.approx(100.0)


def test_urgent_and_zero_flexibility_gives_zero_score_when_alone_greenest():
    # Fully urgent, no flexibility, and already the greenest region ->
    # nothing to gain, score is exactly 0.
    result = score_scheduling(
        current_region="SR",
        region_ci_now={"NR": 500.0, "SR": 400.0},
        ci_window=[400.0],
        urgency_weight=1.0,
        spatial_weight=1.0,
    )
    assert result.temporal_saving == pytest.approx(0.0)
    assert result.spatial_saving == pytest.approx(0.0)
    assert result.score == pytest.approx(0.0)
    assert result.recommend_shift_time is False
    assert result.recommend_shift_region is False
    assert result.best_region == "SR"


def test_spatial_weight_scales_region_term():
    # spatial_weight < 1 should scale the spatial saving down proportionally.
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 500.0, "SR": 300.0},
        ci_window=[500.0],
        urgency_weight=0.0,
        spatial_weight=0.5,
    )
    assert result.spatial_saving == pytest.approx(200.0)
    assert result.score == pytest.approx(100.0)  # 200 * 0.5


def test_partial_urgency_weight_scales_temporal_term():
    # urgency_weight=0.25 -> temporal weighted by 0.75.
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 500.0},  # single region -> no spatial saving
        ci_window=[500.0, 400.0],
        urgency_weight=0.25,
        spatial_weight=1.0,
    )
    assert result.temporal_saving == pytest.approx(100.0)
    assert result.spatial_saving == pytest.approx(0.0)
    assert result.score == pytest.approx(75.0)  # 100 * (1 - 0.25)


def test_tie_break_prefers_current_region():
    # When another region ties the current region's CI, do NOT recommend a
    # pointless move; best_region stays the current one.
    result = score_scheduling(
        current_region="NR",
        region_ci_now={"NR": 400.0, "SR": 400.0},
        ci_window=[400.0],
        urgency_weight=0.0,
        spatial_weight=1.0,
    )
    assert result.best_region == "NR"
    assert result.recommend_shift_region is False
    assert result.spatial_saving == pytest.approx(0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"current_region": "NR", "region_ci_now": {}, "ci_window": [1.0], "urgency_weight": 0.0},
        {"current_region": "NR", "region_ci_now": {"NR": 1.0}, "ci_window": [], "urgency_weight": 0.0},
        {"current_region": "ZZ", "region_ci_now": {"NR": 1.0}, "ci_window": [1.0], "urgency_weight": 0.0},
        {"current_region": "NR", "region_ci_now": {"NR": 1.0}, "ci_window": [1.0], "urgency_weight": 1.5},
        {"current_region": "NR", "region_ci_now": {"NR": 1.0}, "ci_window": [1.0], "urgency_weight": -0.1},
        {"current_region": "NR", "region_ci_now": {"NR": 1.0}, "ci_window": [-1.0], "urgency_weight": 0.0},
        {"current_region": "NR", "region_ci_now": {"NR": -1.0}, "ci_window": [1.0], "urgency_weight": 0.0},
    ],
)
def test_invalid_inputs_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        score_scheduling(**kwargs)


def test_negative_spatial_weight_raises():
    with pytest.raises(ValueError):
        score_scheduling(
            current_region="NR",
            region_ci_now={"NR": 1.0},
            ci_window=[1.0],
            urgency_weight=0.0,
            spatial_weight=-1.0,
        )
