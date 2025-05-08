from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")

logger.info(f"Connecting to MongoDB at {mongodb_uri}")

try:
    client = MongoClient(mongodb_uri)
    smart_parking_db = client.smart_parking
    logger.info("Подключение к базе данных установлено")
except ConnectionFailure as e:
    logger.error(f"Could not connect to MongoDB: {e}")
