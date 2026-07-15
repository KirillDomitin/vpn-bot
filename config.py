import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
SERVER_IP = os.getenv("SERVER_IP", "138.124.75.147")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "amnezia-awg")
WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
SUBNET = os.getenv("SUBNET", "10.8.1.0/24")
DNS = os.getenv("DNS", "1.1.1.1, 1.0.0.1")
DB_PATH = os.getenv("DB_PATH", "bot.db")
