from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from database import smart_parking_db
from models import VehicleArrival
from datetime import datetime, timedelta
from pymongo import ReturnDocument
import json
import uuid

app = FastAPI(
    title="Smart Parking Management System API",
    description="FastAPI-приложение для автоматизации работы парковки с поддержкой WebSocket для обновлений в реальном времени.",
    version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Конфигурация
PARKING_CAPACITY = 50  # Общее количество мест
TARIFFS = {  # Тарифы в рублях/минуту
    "car": 1.5,
    "disCar": 2,
    "evCar": 2.5
}

# Инициализация парковочных мест при первом запуске
def initialize_parking(parkingCapacity = PARKING_CAPACITY):
    if smart_parking_db.spots.count_documents({}) == 0:
        spots = []
        for i in range(parkingCapacity):
            spot_type = "regular"
            if i < 3: spot_type = "disabled"
            elif 3 <= i < 5: spot_type = "ev"
            
            spots.append({
                "spot_id": i,
                "spot_type": spot_type,
                "status": "free",
                "current_vehicle": None
            })
        smart_parking_db.spots.insert_many(spots)

initialize_parking()

# WebSocket менеджер
class ConnectionManager:
    def __init__(self):
        self.active_connections = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Поддерживаем соединение
    except:
        manager.disconnect(websocket)

def find_available_spot(vehicle_type: str) -> dict:
    """Поиск подходящего свободного места"""
    query = {"status": "free"}
    
    # Определяем подходящие типы мест для ТС
    if vehicle_type == "car":
        query["spot_type"] = "regular"
    elif vehicle_type == "evCar":
        query["spot_type"] = "ev"
    elif vehicle_type == "disCar":
        query["spot_type"] = "disabled"
    else:  # the type of transport is not supported
        return None
    
    spot = smart_parking_db.spots.find_one_and_update(
        query,
        {"$set": {"status": "occupied"}},
        return_document=ReturnDocument.AFTER
    )
    
    return spot

@app.post("/api/vehicle/arrive")
async def vehicle_arrive(vehicle: VehicleArrival):
    """Регистрация заезда"""
    spot = find_available_spot(vehicle.vehicle_type)
    
    if not spot:
        raise HTTPException(status_code=400, detail="No available spots")
    
    # Создаем запись о ТС
    vehicle_data = {
        "id": str(uuid.uuid4()),
        "type": vehicle.vehicle_type,
        "entry_time": datetime.now(),
        "exit_time": None,
        "spot_id": spot["spot_id"],
        "paid": False
    }
    
    smart_parking_db.vehicles.insert_one(vehicle_data)
    
    # Обновляем место
    smart_parking_db.spots.update_one(
        {"spot_id": spot["spot_id"]},
        {"$set": {"current_vehicle": vehicle_data["id"]}}
    )
    
    # Отправляем обновление через WebSocket
    await manager.broadcast(json.dumps({
        "event": "vehicle_arrived",
        "spot_id": spot["spot_id"],
        "vehicle_id": vehicle_data["id"]
    }))
    
    return {"status": "success", "spot_id": spot["spot_id"]}

@app.post("/api/vehicle/depart/{vehicle_id}")
async def vehicle_depart(vehicle_id: str):
    """Обработка выезда и расчет оплаты"""
    vehicle = smart_parking_db.vehicles.find_one({"id": vehicle_id})
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    if vehicle.get("paid", False):
        raise HTTPException(status_code=400, detail="Already paid")
    
    # Расчет времени и стоимости
    try:
        tariff = TARIFFS[vehicle["type"]]
    except:
        tariff = 0

    exit_time = datetime.now()
    entry_time = vehicle["entry_time"]
    duration = (exit_time - entry_time).total_seconds() / 60  # в минутах
    cost = duration * tariff
    
    # Обновляем запись ТС
    smart_parking_db.vehicles.update_one(
        {"id": vehicle_id},
        {"$set": {
            "exit_time": exit_time,
            "cost": round(cost, 2),
            "paid": True
        }}
    )
    
    # Освобождаем место
    smart_parking_db.spots.update_one(
        {"spot_id": vehicle["spot_id"]},
        {"$set": {"status": "free", "current_vehicle": None}}
    )
    
    # Отправляем обновление
    await manager.broadcast(json.dumps({
        "event": "vehicle_departed",
        "spot_id": vehicle["spot_id"],
        "vehicle_id": vehicle_id
    }))
    
    return {
        "status": "success",
        "cost": round(cost, 2),
        "duration_minutes": round(duration, 1)
    }

@app.get("/api/parking/status")
async def get_status():
    """Текущее состояние парковки"""
    spots = list(smart_parking_db.spots.find({}, {"_id": 0}))
    return {"status": "success", "data": spots}

@app.get("/api/stats")
async def get_stats(days: int = 1):
    """Получение статистики"""
    # Расчет временного диапазона
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Агрегация данных
    pipeline = [
        {"$match": {"entry_time": {"$gte": start_date}}},
        {"$group": {
            "_id": {"$hour": "$entry_time"},
            "total_vehicles": {"$sum": 1},
            "total_revenue": {"$sum": "$cost"}
        }},
        {"$sort": {"_id": 1}}
    ]
    
    hourly_stats = list(smart_parking_db.vehicles.aggregate(pipeline))
    
    # Форматирование результата
    formatted_stats = [{
        "hour": stat["_id"],
        "vehicles": stat["total_vehicles"],
        "revenue": stat.get("total_revenue", 0)
    } for stat in hourly_stats]
    
    return {"status": "success", "data": formatted_stats}

@app.get("/api/vehicles")
async def get_active_vehicles():
    vehicles = list(smart_parking_db.vehicles.find({"exit_time": None}, {"_id": 0}))
    return {"status": "success", "data": vehicles}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)