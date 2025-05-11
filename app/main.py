from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from database import smart_parking_db
from models import VehicleArrival
from datetime import datetime, timedelta
from typing import List

app = FastAPI(
    title="Smart Parking Management System API",
    description="FastAPI-приложение для автоматизации работы парковки",
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
PARKING_CAPACITY = 16  # Общее количество мест
TARIFFS = {  # Тарифы в рублях/минуту
    "car": 1.5,
    "evCar": 2.5
}

# Инициализация парковочных мест при первом запуске
def initialize_parking(parkingCapacity = PARKING_CAPACITY):
    if smart_parking_db.spots.count_documents({}) == 0:
        spots = []
        for i in range(parkingCapacity):
            spot_type = "regular"
            if 13 < i <= 15: spot_type = "ev"
            
            spots.append({
                "spot_id": i,
                "spot_type": spot_type,
                "status": "free",
                "current_vehicle": None
            })
        smart_parking_db.spots.insert_many(spots)

initialize_parking()

def record_parking_load():
    """Запись текущей загруженности парковки"""
    total_spots = PARKING_CAPACITY
    occupied_spots = smart_parking_db.spots.count_documents({"status": "occupied"})
    load_percentage = (occupied_spots / total_spots) * 100

    load_history = {
        "timestamp": datetime.now(),
        "occupied_spots": occupied_spots,
        "total_spots": total_spots,
        "load_percentage": round(load_percentage, 2)
    }
    
    smart_parking_db.parking_load_history.insert_one(load_history)

@app.post("/api/vehicle/arrive")
async def vehicle_arrive(vehicle: VehicleArrival):
    """Регистрация заезда на определенное место"""
    # Проверяем соответствие типа места и типа транспортного средства
    spot = smart_parking_db.spots.find_one({"spot_id": vehicle.spot_id})
    
    if not spot:
        raise HTTPException(
            status_code=400,
            detail=f"Парковочное место {vehicle.spot_id} не существует"
        )
    
    # Проверяем, что электромобиль паркуется только на специальных местах
    if vehicle.isEv and vehicle.spot_id not in [14, 15]:
        raise HTTPException(
            status_code=400,
            detail="Электромобиль может парковаться только на местах 14 и 15"
        )
    
    # Проверяем, что обычный автомобиль не паркуется на местах для электромобилей
    if not vehicle.isEv and vehicle.spot_id in [14, 15]:
        raise HTTPException(
            status_code=400,
            detail="Обычный автомобиль не может парковаться на местах для электромобилей (14 и 15)"
        )
    
    # Проверяем, не занято ли место
    if spot["status"] == "occupied":
        raise HTTPException(
            status_code=400,
            detail=f"Парковочное место {vehicle.spot_id} уже занято"
        )
    
    # Проверяем, нет ли уже такого транспортного средства в базе
    existing_vehicle = smart_parking_db.vehicles.find_one({
        "id": vehicle.vehicle_id,
        "exit_time": None  # Ищем только активные записи
    })
    
    if existing_vehicle:
        raise HTTPException(
            status_code=400,
            detail=f"Транспортное средство {vehicle.vehicle_id} уже находится на парковке"
        )
    
    # Создаем запись о ТС
    vehicle_data = {
        "id": vehicle.vehicle_id,
        "isEv": vehicle.isEv,
        "type": vehicle.type,
        "entry_time": datetime.now(),
        "exit_time": None,
        "spot_id": vehicle.spot_id,
        "paid": False
    }
    
    # Обновляем место
    update_result = smart_parking_db.spots.update_one(
        {"spot_id": vehicle_data["spot_id"]},
        {"$set": {"current_vehicle": vehicle_data["id"], "status": "occupied"}}
    )
    
    # Проверяем, что обновление прошло успешно
    if update_result.modified_count == 0:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось занять парковочное место {vehicle.spot_id}"
        )
    
    # Создаем запись о транспортном средстве
    smart_parking_db.vehicles.insert_one(vehicle_data)
    
    # Записываем загруженность
    record_parking_load()
        
    return {"status": "success"}

@app.post("/api/vehicle/depart/{vehicle_id}")
async def vehicle_depart(vehicle_id: str):
    """Обработка выезда и расчет оплаты"""
    vehicle = smart_parking_db.vehicles.find_one({"id": vehicle_id})
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    if vehicle.get("paid", False):
        raise HTTPException(status_code=400, detail="Already paid")
    
    # Расчет времени и стоимости
    if vehicle["isEv"]:
        tariff = TARIFFS["evCar"]
    else:
        tariff = TARIFFS["car"]

    exit_time = datetime.now()
    entry_time = vehicle["entry_time"]
    duration = (exit_time - entry_time).total_seconds() / 60  # в минутах
    cost = duration * tariff
    
    # Создаем запись в истории
    history_entry = {
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle["type"],
        "spot_id": vehicle["spot_id"],
        "entry_time": entry_time,
        "exit_time": exit_time,
        "duration_minutes": round(duration, 1),
        "cost": round(cost, 2)
    }
    smart_parking_db.parking_history.insert_one(history_entry)
    
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
    
    # Записываем загруженность
    record_parking_load()
        
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

@app.post("/api/reset")
async def reset_collections():
    """Сброс коллекций vehicles и spots"""
    try:
        # Удаляем все документы из коллекции vehicles
        smart_parking_db.vehicles.delete_many({})
        
        # Удаляем все документы из коллекции spots
        smart_parking_db.spots.delete_many({})
        
        # Переинициализируем парковочные места
        initialize_parking()
        
        return {
            "status": "success",
            "message": "Коллекции vehicles и spots успешно сброшены"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при сбросе коллекций: {str(e)}"
        )

def generate_time_intervals(start_time: datetime, end_time: datetime, interval: str) -> List[datetime]:
    """Генерация временных интервалов"""
    intervals = []
    current = start_time
    
    if interval == "10s":
        delta = timedelta(seconds=10)
    elif interval == "1m":
        delta = timedelta(minutes=1)
    elif interval == "5m":
        delta = timedelta(minutes=5)
    elif interval == "15m":
        delta = timedelta(minutes=15)
    else:  # 1h
        delta = timedelta(hours=1)
    
    while current <= end_time:
        intervals.append(current)
        current += delta
    
    return intervals

def format_timestamp(dt: datetime, interval: str) -> str:
    """Форматирование временной метки в зависимости от интервала"""
    if interval == "10s":
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    elif interval == "1m":
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    elif interval == "5m":
        return dt.strftime("%Y-%m-%d %H:%M")
    elif interval == "15m":
        return dt.strftime("%Y-%m-%d %H:%M")
    else:  # 1h
        return dt.strftime("%Y-%m-%d %H")

@app.get("/api/stats/vehicles")
async def get_vehicles_stats(
    time_range: str = Query("1h", enum=["1m", "10m", "1h", "1d"]),
    interval: str = Query("1m", enum=["10s", "1m", "5m", "15m", "1h"])
):
    """Статистика по количеству машин"""
    end_time = datetime.now()
    
    # Определяем начальное время в зависимости от выбранного диапазона
    if time_range == "1m":
        start_time = end_time - timedelta(minutes=1)
    elif time_range == "10m":
        start_time = end_time - timedelta(minutes=10)
    elif time_range == "1h":
        start_time = end_time - timedelta(hours=1)
    else:  # 1d
        start_time = end_time - timedelta(days=1)

    # Генерируем все интервалы
    intervals = generate_time_intervals(start_time, end_time, interval)
    
    # Получаем данные из базы
    pipeline = [
        {
            "$match": {
                "timestamp": {"$gte": start_time, "$lte": end_time}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": format_timestamp(datetime.now(), interval),
                        "date": "$timestamp"
                    }
                },
                "count": {"$sum": 1},
                "occupied_spots": {"$avg": "$occupied_spots"},
                "load_percentage": {"$avg": "$load_percentage"}
            }
        }
    ]
    
    stats = list(smart_parking_db.parking_load_history.aggregate(pipeline))
    
    # Создаем словарь с данными
    stats_dict = {stat["_id"]: stat for stat in stats}
    
    # Формируем результат со всеми интервалами
    result = []
    for dt in intervals:
        timestamp = format_timestamp(dt, interval)
        if timestamp in stats_dict:
            stat = stats_dict[timestamp]
            result.append({
                "timestamp": timestamp,
                "count": stat["count"],
                "occupied_spots": round(stat["occupied_spots"], 2),
                "load_percentage": round(stat["load_percentage"], 2)
            })
        else:
            result.append({
                "timestamp": timestamp,
                "count": 0,
                "occupied_spots": 0,
                "load_percentage": 0
            })
    
    return {
        "status": "success",
        "data": result
    }

@app.get("/api/stats/revenue")
async def get_revenue_stats(
    time_range: str = Query("1h", enum=["1m", "10m", "1h", "1d"]),
    interval: str = Query("1m", enum=["10s", "1m", "5m", "15m", "1h"])
):
    """Статистика по выручке"""
    end_time = datetime.now()
    
    if time_range == "1m":
        start_time = end_time - timedelta(minutes=1)
    elif time_range == "10m":
        start_time = end_time - timedelta(minutes=10)
    elif time_range == "1h":
        start_time = end_time - timedelta(hours=1)
    else:  # 1d
        start_time = end_time - timedelta(days=1)

    # Генерируем все интервалы
    intervals = generate_time_intervals(start_time, end_time, interval)
    
    pipeline = [
        {
            "$match": {
                "exit_time": {"$gte": start_time, "$lte": end_time}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": format_timestamp(datetime.now(), interval),
                        "date": "$exit_time"
                    }
                },
                "revenue": {"$sum": "$cost"},
                "count": {"$sum": 1}
            }
        }
    ]
    
    stats = list(smart_parking_db.parking_history.aggregate(pipeline))
    
    # Создаем словарь с данными
    stats_dict = {stat["_id"]: stat for stat in stats}
    
    # Формируем результат со всеми интервалами
    result = []
    for dt in intervals:
        timestamp = format_timestamp(dt, interval)
        if timestamp in stats_dict:
            stat = stats_dict[timestamp]
            result.append({
                "timestamp": timestamp,
                "revenue": round(stat["revenue"], 2),
                "count": stat["count"]
            })
        else:
            result.append({
                "timestamp": timestamp,
                "revenue": 0,
                "count": 0
            })
    
    return {
        "status": "success",
        "data": result
    }

@app.get("/api/stats/duration")
async def get_duration_stats(
    time_range: str = Query("1h", enum=["1m", "10m", "1h", "1d"]),
    interval: str = Query("1m", enum=["10s", "1m", "5m", "15m", "1h"])
):
    """Статистика по времени стоянки"""
    end_time = datetime.now()
    
    if time_range == "1m":
        start_time = end_time - timedelta(minutes=1)
    elif time_range == "10m":
        start_time = end_time - timedelta(minutes=10)
    elif time_range == "1h":
        start_time = end_time - timedelta(hours=1)
    else:  # 1d
        start_time = end_time - timedelta(days=1)

    # Генерируем все интервалы
    intervals = generate_time_intervals(start_time, end_time, interval)
    
    pipeline = [
        {
            "$match": {
                "exit_time": {"$gte": start_time, "$lte": end_time}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": format_timestamp(datetime.now(), interval),
                        "date": "$exit_time"
                    }
                },
                "avg_duration": {"$avg": "$duration_minutes"},
                "min_duration": {"$min": "$duration_minutes"},
                "max_duration": {"$max": "$duration_minutes"},
                "count": {"$sum": 1}
            }
        }
    ]
    
    stats = list(smart_parking_db.parking_history.aggregate(pipeline))
    
    # Создаем словарь с данными
    stats_dict = {stat["_id"]: stat for stat in stats}
    
    # Формируем результат со всеми интервалами
    result = []
    for dt in intervals:
        timestamp = format_timestamp(dt, interval)
        if timestamp in stats_dict:
            stat = stats_dict[timestamp]
            result.append({
                "timestamp": timestamp,
                "avg_duration": round(stat["avg_duration"], 2),
                "min_duration": round(stat["min_duration"], 2),
                "max_duration": round(stat["max_duration"], 2),
                "count": stat["count"]
            })
        else:
            result.append({
                "timestamp": timestamp,
                "avg_duration": 0,
                "min_duration": 0,
                "max_duration": 0,
                "count": 0
            })
    
    return {
        "status": "success",
        "data": result
    }

@app.get("/api/stats/total-revenue")
async def get_total_revenue():
    """Общая выручка"""
    pipeline = [
        {
            "$group": {
                "_id": None,
                "total_revenue": {"$sum": "$cost"}
            }
        }
    ]
    
    result = list(smart_parking_db.parking_history.aggregate(pipeline))
    total_revenue = result[0]["total_revenue"] if result else 0
    
    return {
        "status": "success",
        "data": {
            "total_revenue": round(total_revenue, 2)
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)