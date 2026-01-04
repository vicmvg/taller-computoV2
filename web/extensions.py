# web/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_socketio import SocketIO

db = SQLAlchemy()
cache = Cache()
socketio = SocketIO()  # ✅ NUEVO