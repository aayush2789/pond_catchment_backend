"""Transparent water-volume estimation (Phase 6).

Methodology (documented, standard water-balance approach):

    theoretical runoff (m³) = catchment area (m²) × rainfall depth (m) × runoff coefficient
    expected collectible water (m³) = theoretical runoff × collection efficiency

- The runoff coefficient (0 < C < 1) expresses the fraction of rainfall on the
  catchment that becomes surface runoff. For small rural/mixed agricultural
  catchments, published guidance (e.g. USDA SCS, FAO) places typical values in
  the 0.2-0.5 range depending on slope, soil and land cover. The default used
  here (0.30) sits at the middle of that range and must be treated as a
  planning-level assumption, not a measured value. Callers may pass an explicit
  coefficient to override it, and the basis is always reported.

- The collection efficiency accounts for conveyance, seepage and evaporation
  losses between runoff generation and actual storage in the pond. A planning
  default of 0.75 is used and reported explicitly.

- Unit chain: area m² × rainfall (mm / 1000 = m) = m³, so volumes are cubic
  metres by construction.
"""

from typing import Optional

from fastapi import HTTPException, status

from app.schemas.water import WaterVolumeResult

# Documented planning defaults (see module docstring for their origin).
DEFAULT_RUNOFF_COEFFICIENT = 0.30
RUNOFF_COEFFICIENT_BASIS = (
    "Default 0.30 for a small rural/mixed-agricultural catchment "
    "(typical literature range 0.2-0.5, USDA SCS / FAO guidance)."
)
DEFAULT_COLLECTION_EFFICIENCY = 0.75
EFFICIENCY_BASIS = (
    "Planning assumption of 75%: allowance for conveyance, seepage and "
    "evaporation losses between runoff generation and pond storage."
)


class WaterVolumeService:
    @staticmethod
    def estimate(
        catchment_area_m2: float,
        rainfall_mm: float,
        rainfall_period: str,
        runoff_coefficient: Optional[float] = None,
        collection_efficiency: Optional[float] = None,
    ) -> WaterVolumeResult:
        if catchment_area_m2 <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Catchment area must be positive for runoff estimation.",
            )
        if rainfall_mm < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rainfall depth cannot be negative.",
            )

        coefficient = (
            runoff_coefficient if runoff_coefficient is not None else DEFAULT_RUNOFF_COEFFICIENT
        )
        efficiency = (
            collection_efficiency
            if collection_efficiency is not None
            else DEFAULT_COLLECTION_EFFICIENCY
        )
        if not (0.0 <= coefficient <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Runoff coefficient must be within [0, 1].",
            )
        if not (0.0 <= efficiency <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Collection efficiency must be within [0, 1].",
            )

        rainfall_m = rainfall_mm / 1000.0
        theoretical = catchment_area_m2 * rainfall_m * coefficient
        collectible = theoretical * efficiency

        basis = RUNOFF_COEFFICIENT_BASIS
        if runoff_coefficient is not None:
            basis = "Explicitly provided in the analysis parameters."

        return WaterVolumeResult(
            catchment_area_m2=round(catchment_area_m2, 2),
            rainfall_mm=round(rainfall_mm, 1),
            rainfall_period=rainfall_period,
            runoff_coefficient=round(coefficient, 3),
            runoff_coefficient_basis=basis,
            theoretical_runoff_m3=round(theoretical, 2),
            collection_efficiency=round(efficiency, 3),
            efficiency_basis=EFFICIENCY_BASIS,
            expected_collectible_water_m3=round(collectible, 2),
            assumptions=[
                "Runoff volume = catchment area x rainfall depth x runoff coefficient "
                "(rainfall converted from mm to m).",
                "Expected collectible water = theoretical runoff x collection efficiency.",
                "The runoff coefficient and collection efficiency are planning-level "
                "assumptions, not measured values; both are reported explicitly.",
            ],
        )
