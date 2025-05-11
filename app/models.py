from pydantic import BaseModel
from datetime import datetime

class VehicleArrival(BaseModel):
    isEv: bool
    type: str
    spot_id: int
    vehicle_id: str
    timestamp: datetime = datetime.now()

class ParkingSpot(BaseModel):
    spot_id: int
    spot_type: str  # "regular", "ev"
    status: str  # "free", "occupied"

class ParkingHistory(BaseModel):
    vehicle_id: str
    vehicle_type: str
    spot_id: int
    entry_time: datetime
    exit_time: datetime
    duration_minutes: float
    cost: float

class ParkingLoadHistory(BaseModel):
    timestamp: datetime
    occupied_spots: int
    total_spots: int
    load_percentage: float