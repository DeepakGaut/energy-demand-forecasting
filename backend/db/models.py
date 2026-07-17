"""
backend/db/models.py

SQLAlchemy ORM models for:
  - ci_timeseries : daily/hourly carbon intensity + fuel mix per region
  - hardware_specs : lookup table mapping CPU/GPU model -> TDP (Watts)

These are the source of truth for the schema. Alembic will generate the
migration from these definitions (see Step 4 below).
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    UniqueConstraint,
    Index,
)

from .database import Base


class CITimeseries(Base):
    __tablename__ = "ci_timeseries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    region = Column(String(10), nullable=False)          # e.g. "NR", "WR"
    timestamp = Column(DateTime(timezone=True), nullable=False)

    ci_gco2e_per_kwh = Column(Float, nullable=False)      # carbon intensity

    coal_pct = Column(Float, nullable=True)
    hydro_pct = Column(Float, nullable=True)
    wind_pct = Column(Float, nullable=True)
    solar_pct = Column(Float, nullable=True)
    nuclear_pct = Column(Float, nullable=True)
    gas_pct = Column(Float, nullable=True)

    __table_args__ = (
        # Prevent duplicate rows for the same region + timestamp
        UniqueConstraint("region", "timestamp", name="uq_region_timestamp"),
        # Speed up the most common query pattern: "give me CI for region X
        # over a time range" (used heavily by the scheduling engine)
        Index("ix_ci_timeseries_region_timestamp", "region", "timestamp"),
    )

    def __repr__(self):
        return (
            f"<CITimeseries region={self.region} timestamp={self.timestamp} "
            f"ci={self.ci_gco2e_per_kwh}>"
        )


class HardwareSpecs(Base):
    __tablename__ = "hardware_specs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(100), nullable=False, unique=True)  # e.g. "NVIDIA A100"
    hardware_type = Column(String(10), nullable=False)             # "CPU" or "GPU"
    tdp_watts = Column(Float, nullable=False)                      # Thermal Design Power

    def __repr__(self):
        return f"<HardwareSpecs {self.model_name} ({self.hardware_type}) {self.tdp_watts}W>"