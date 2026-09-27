import pytest
from fastapi import HTTPException, status

from app.services.pond import PondStorageService
from app.services.water import WaterVolumeService


# --- Phase 6: water volume --------------------------------------------------------------


def test_runoff_calculation_and_units():
    # 10,000 m2 x 1000 mm (1 m) x 0.30 = 3,000 m3 theoretical.
    result = WaterVolumeService.estimate(10_000.0, 1000.0, "2015-2024")
    assert result.theoretical_runoff_m3 == pytest.approx(3000.0)
    assert result.expected_collectible_water_m3 == pytest.approx(3000.0 * 0.75)
    assert result.runoff_coefficient == 0.30
    assert result.collection_efficiency == 0.75
    assert result.rainfall_period == "2015-2024"


def test_explicit_coefficient_overrides_default():
    result = WaterVolumeService.estimate(10_000.0, 500.0, "p", runoff_coefficient=0.5)
    assert result.theoretical_runoff_m3 == pytest.approx(2500.0)
    assert "explicitly provided" in result.runoff_coefficient_basis.lower()


def test_efficiency_distinguishes_collectible_from_theoretical():
    result = WaterVolumeService.estimate(1000.0, 100.0, "p")
    assert result.expected_collectible_water_m3 < result.theoretical_runoff_m3


def test_zero_rainfall_gives_zero_volume():
    result = WaterVolumeService.estimate(10_000.0, 0.0, "p")
    assert result.theoretical_runoff_m3 == 0.0
    assert result.expected_collectible_water_m3 == 0.0


def test_invalid_inputs_rejected():
    with pytest.raises(HTTPException) as area_exc:
        WaterVolumeService.estimate(0.0, 100.0, "p")
    assert area_exc.value.status_code == status.HTTP_400_BAD_REQUEST

    with pytest.raises(HTTPException):
        WaterVolumeService.estimate(1000.0, -5.0, "p")

    with pytest.raises(HTTPException):
        WaterVolumeService.estimate(1000.0, 100.0, "p", runoff_coefficient=1.5)

    with pytest.raises(HTTPException):
        WaterVolumeService.estimate(1000.0, 100.0, "p", collection_efficiency=-0.1)


# --- Phase 7: pond storage ---------------------------------------------------------------


def test_storage_capacity_matches_inflow():
    result = PondStorageService.suggest_pond_storage(1500.0)
    assert result.storage_capacity_m3 == pytest.approx(1500.0, rel=0.01)
    assert result.design_inflow_m3 == 1500.0
    assert result.depth_m == 3.0
    assert result.top_width_m > result.bottom_width_m
    assert result.surface_area_m2 == pytest.approx(result.top_width_m * result.top_length_m, rel=0.01)
    assert "not a substitute" in result.note.lower()


def test_storage_scales_monotonically_with_inflow():
    small = PondStorageService.suggest_pond_storage(500.0)
    large = PondStorageService.suggest_pond_storage(5000.0)
    assert large.bottom_width_m > small.bottom_width_m
    assert large.storage_capacity_m3 > small.storage_capacity_m3


def test_storage_depth_override():
    result = PondStorageService.suggest_pond_storage(1000.0, max_depth_m=1.5)
    assert result.depth_m == 1.5
    assert result.storage_capacity_m3 == pytest.approx(1000.0, rel=0.01)
    # Shallower basin needs a larger footprint.
    deep = PondStorageService.suggest_pond_storage(1000.0, max_depth_m=3.0)
    assert result.surface_area_m2 > deep.surface_area_m2


def test_storage_invalid_inputs_rejected():
    with pytest.raises(HTTPException):
        PondStorageService.suggest_pond_storage(0.0)
    with pytest.raises(HTTPException):
        PondStorageService.suggest_pond_storage(100.0, max_depth_m=50.0)
    with pytest.raises(HTTPException):
        PondStorageService.suggest_pond_storage(100.0, side_slope_hv=-1.0)
