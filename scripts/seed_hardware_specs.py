"""
backend/scripts/seed_hardware_specs.py

Populates the hardware_specs lookup table with common CPU/GPU models and
their TDP (Thermal Design Power, in Watts). Extend this list as your
scheduling engine needs to support more hardware.

TDP values below are manufacturer-published figures — double check any
you add against the vendor's official spec sheet, since TDP definitions
vary slightly between Intel/AMD/NVIDIA and marketing figures sometimes
differ from real-world sustained draw.

Run:
    python backend/scripts/seed_hardware_specs.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from db.database import SessionLocal
from db.models import HardwareSpecs

HARDWARE_SEED_DATA = [
    # --- CPUs ---
    {"model_name": "Intel Xeon Platinum 8480+", "hardware_type": "CPU", "tdp_watts": 350},
    {"model_name": "Intel Xeon Gold 6338", "hardware_type": "CPU", "tdp_watts": 205},
    {"model_name": "AMD EPYC 9654", "hardware_type": "CPU", "tdp_watts": 360},
    {"model_name": "AMD EPYC 7763", "hardware_type": "CPU", "tdp_watts": 280},
    {"model_name": "Intel Core i9-13900K", "hardware_type": "CPU", "tdp_watts": 253},
    {"model_name": "AMD Ryzen 9 7950X", "hardware_type": "CPU", "tdp_watts": 170},

    # --- GPUs ---
    {"model_name": "NVIDIA A100 80GB", "hardware_type": "GPU", "tdp_watts": 400},
    {"model_name": "NVIDIA H100 SXM", "hardware_type": "GPU", "tdp_watts": 700},
    {"model_name": "NVIDIA V100", "hardware_type": "GPU", "tdp_watts": 300},
    {"model_name": "NVIDIA RTX 4090", "hardware_type": "GPU", "tdp_watts": 450},
    {"model_name": "NVIDIA RTX 3090", "hardware_type": "GPU", "tdp_watts": 350},
    {"model_name": "AMD MI300X", "hardware_type": "GPU", "tdp_watts": 750},
]


def seed():
    db = SessionLocal()
    try:
        inserted, skipped = 0, 0
        for entry in HARDWARE_SEED_DATA:
            existing = (
                db.query(HardwareSpecs)
                .filter(HardwareSpecs.model_name == entry["model_name"])
                .first()
            )
            if existing:
                skipped += 1
                continue
            db.add(HardwareSpecs(**entry))
            inserted += 1
        db.commit()
        print(f"Inserted {inserted} row(s), skipped {skipped} already-existing row(s).")
    finally:
        db.close()


if __name__ == "__main__":
    seed()