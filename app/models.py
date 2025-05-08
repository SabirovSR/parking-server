from pydantic import BaseModel
from datetime import datetime

class VehicleArrival(BaseModel):
    vehicle_type: str  # "car", "bus", etc
    timestamp: datetime = datetime.now()

class ParkingSpot(BaseModel):
    spot_id: int
    spot_type: str  # "regular", "disabled", etc
    status: str  # "free", "occupied"