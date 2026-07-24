from dataclasses import dataclass

@dataclass
class Thresholds:
    """Thresholds for plant"""
    soil_moisture_min: float = None
    air_moisture_min: float = None
    temp_min: float = None
    temp_max: float = None
    light_min: float = None
    light_max: float = None

@dataclass
class NeedKeys:
    """Store Firestore keys for each active plant need entry."""
    water: str = None
    spray: str = None
    hot: str = None
    cold: str = None
    low_light: str = None
    high_light: str = None