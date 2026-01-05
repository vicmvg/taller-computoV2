# gunicorn_config.py
"""
Configuración de Gunicorn para Flask en Render
Optimizado para WebSockets con eventlet
"""

import os

# Binding
bind = "0.0.0.0:" + str(os.environ.get("PORT", 8000))

# ⚠️ IMPORTANTE: WebSockets requiere 1 solo worker
# Con eventlet, este worker puede manejar miles de conexiones concurrentes
workers = 1

# ❌ Threads NO se usan con eventlet (eventlet maneja concurrencia internamente)
# threads = 4  # COMENTADO - no aplica con eventlet

# ✅ Worker class para WebSockets
worker_class = "eventlet"  # CRÍTICO para WebSockets

# Timeouts
timeout = 120  # Aumentado a 120s para conexiones WebSocket persistentes
graceful_timeout = 30
keepalive = 5

# Recycling - DESHABILITADO para WebSockets
# Con WebSockets persistentes, reciclar workers corta las conexiones
max_requests = 0  # 0 = nunca reciclar
max_requests_jitter = 0

# Logging
accesslog = "-"  # Logs a stdout
errorlog = "-"   # Errors a stdout
loglevel = "info"

# Process naming
proc_name = "flask_taller_computo_ws"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Preload
preload_app = False  # False para eventlet (evita problemas)

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# ✅ CONFIGURACIÓN ESPECÍFICA DE EVENTLET
worker_connections = 1000  # Cada worker puede manejar 1000 conexiones simultáneas

def when_ready(server):
    """Se ejecuta cuando el servidor está listo"""
    print("=" * 60)
    print("🚀 Gunicorn con WebSockets está listo")
    print(f"   Worker class: {worker_class}")
    print(f"   Workers: {workers}")
    print(f"   Conexiones por worker: {worker_connections}")
    print(f"   Capacidad total: ~{worker_connections} conexiones WebSocket")
    print(f"   Timeout: {timeout}s")
    print("=" * 60)

def worker_int(worker):
    """Se ejecuta cuando un worker recibe SIGINT"""
    print(f"⚠️  Worker {worker.pid} recibió SIGINT")

def pre_fork(server, worker):
    """Se ejecuta antes de hacer fork del worker"""
    pass

def post_fork(server, worker):
    """Se ejecuta después de hacer fork del worker"""
    print(f"✅ Worker eventlet spawneado (PID: {worker.pid})")

def pre_exec(server):
    """Se ejecuta antes de ejecutar el nuevo maestro"""
    print("🔄 Ejecutando nuevo maestro...")

def worker_exit(server, worker):
    """Se ejecuta cuando un worker termina"""
    print(f"👋 Worker terminado (PID: {worker.pid})")

def on_exit(server):
    """Se ejecuta cuando el servidor se apaga"""
    print("🛑 Servidor Gunicorn detenido")