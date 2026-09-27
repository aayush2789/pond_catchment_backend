"""Indicative pond storage estimation (Phase 7).

The immediate required output of the system is the expected collectible water
volume (Phase 6). This module keeps pond *storage* a clearly separate concept and
provides a light, documented sizing model so storage can be extended later:

    Basin model: truncated rectangular pyramid (frustum).
    Capacity  : V = depth * (A_bottom + A_top) / 2   (average end areas)
    Top dims  : bottom dims + 2 * side_slope * depth (1:2 H:V side slopes default)

The basin is sized so its capacity matches the expected collectible inflow
(allowing the pond to fill within the analysis period). All outputs are
planning-level indications, not engineering designs.
"""

from fastapi import HTTPException, status

from app.schemas.pond import PondStorageResult

DEFAULT_MAX_DEPTH_M = 3.0
DEFAULT_SIDE_SLOPE_HV = 2.0  # horizontal : vertical, typical small earthen pond
DEFAULT_LENGTH_TO_WIDTH_RATIO = 1.5
_MAX_ITERATIONS = 60  # bisection iterations -> width resolution < 1e-6 m


class PondStorageService:
    @staticmethod
    def _capacity_for_bottom_width(bottom_width: float, depth: float, side_slope: float, ratio: float) -> float:
        bottom_length = bottom_width * ratio
        top_width = bottom_width + 2.0 * side_slope * depth
        top_length = bottom_length + 2.0 * side_slope * depth
        area_bottom = bottom_width * bottom_length
        area_top = top_width * top_length
        return depth * (area_bottom + area_top) / 2.0

    @classmethod
    def suggest_pond_storage(
        cls,
        expected_collectible_water_m3: float,
        max_depth_m: float = DEFAULT_MAX_DEPTH_M,
        side_slope_hv: float = DEFAULT_SIDE_SLOPE_HV,
        length_to_width_ratio: float = DEFAULT_LENGTH_TO_WIDTH_RATIO,
    ) -> PondStorageResult:
        if expected_collectible_water_m3 <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expected collectible water must be positive to size a pond.",
            )
        if max_depth_m <= 0 or max_depth_m > 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pond depth must be within (0, 20] meters.",
            )
        if side_slope_hv <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Side slope must be positive.",
            )
        if length_to_width_ratio < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Length-to-width ratio must be >= 1.",
            )

        target = expected_collectible_water_m3

        def capacity(width: float) -> float:
            return cls._capacity_for_bottom_width(width, max_depth_m, side_slope_hv, length_to_width_ratio)

        # Bisect the bottom width until capacity matches the inflow volume.
        lo, hi = 0.0, 1.0
        while capacity(hi) < target:
            hi *= 2.0
            if hi > 1e6:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expected inflow too large to size an indicative pond basin.",
                )
        for _ in range(_MAX_ITERATIONS):
            mid = (lo + hi) / 2.0
            if capacity(mid) < target:
                lo = mid
            else:
                hi = mid

        bottom_width = hi
        bottom_length = bottom_width * length_to_width_ratio
        top_width = bottom_width + 2.0 * side_slope_hv * max_depth_m
        top_length = bottom_length + 2.0 * side_slope_hv * max_depth_m
        capacity_m3 = capacity(bottom_width)

        return PondStorageResult(
            design_inflow_m3=round(target, 2),
            storage_capacity_m3=round(capacity_m3, 2),
            depth_m=round(max_depth_m, 2),
            bottom_width_m=round(bottom_width, 2),
            bottom_length_m=round(bottom_length, 2),
            top_width_m=round(top_width, 2),
            top_length_m=round(top_length, 2),
            surface_area_m2=round(top_width * top_length, 2),
            side_slope_hv=side_slope_hv,
            length_to_width_ratio=length_to_width_ratio,
            assumptions=[
                "Basin approximated as a truncated rectangular pyramid.",
                f"Capacity = depth x (bottom area + top area) / 2 with depth {max_depth_m} m "
                f"and side slope {side_slope_hv}H:1V.",
                "The basin is sized so capacity ~= expected collectible inflow.",
                "No seepage lining, inlet/outlet structures, or freeboard are modeled.",
            ],
        )
