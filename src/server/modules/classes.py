from dataclasses import dataclass

# =====================================================================
# SECTION 1: Hardware & Environmental Boundaries
# =====================================================================

@dataclass
class Thresholds:
    """
    Data structure defining the ideal environmental limits for the plant.
    These values are loaded from 'config/plant_thresholds.json' and dictates 
    when the hardware triggers an SOS or distress state (e.g., too cold, too dry).
    """
    soil_moisture_min: float = None
    air_moisture_min: float = None
    temp_min: float = None
    temp_max: float = None
    light_min: float = None
    light_max: float = None

# =====================================================================
# SECTION 2: Database Tracking Keys
# =====================================================================

@dataclass
class NeedKeys:
    """
    Data structure that temporarily holds Firestore document IDs for active plant needs.
    When the plant needs something (e.g., water), a database record is created. 
    The ID is stored here until the user fulfills the need, at which point the 
    record is closed and the ID is reset to None.
    """
    water: str = None
    spray: str = None
    hot: str = None
    cold: str = None
    low_light: str = None
    high_light: str = None