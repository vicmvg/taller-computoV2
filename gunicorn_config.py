# gunicorn_config.py
"""
Configuración de Gunicorn para Flask en Render
Optimizado para 2 CPU, 8GB RAM (Plan Starter)
"""

import os
import multiprocessing

# Binding
bind = "0.0.0.0:" + str(os.environ.get("PORT", 8000))

# Workers
# Fórmula: (2 x $num_cores) + 1
# Con 2 CPU: (2 x 2) + 1 = 5 workers
# Pero en Render Starter, usamos 3 para dejar margen
workers = 3

# Threads por worker
# Con 3 workers x 4 threads = 12 requests concurrentes
threads = 4

# Worker class
worker_class = "sync"  # O "gthread" para threads reales

# Timeouts
timeout = 60  # 60 segundos para requests lentos
graceful_timeout = 30
keepalive = 5

# Recycling
max_requests = 1000  # Reciclar worker después de 1000 requests
max_requests_jitter = 100  # Añadir variación aleatoria

# Logging
accesslog = "-"  # Logs a stdout
errorlog = "-"   # Errors a stdout
loglevel = "info"

# Process naming
proc_name = "flask_taller_computo"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Preload
preload_app = True  # Cargar app antes de fork (ahorra RAM)

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

def when_ready(server):
    """Se ejecuta cuando el servidor está listo"""
    print("=" * 60)
    print("🚀 Gunicorn está listo para recibir conexiones")
    print(f"   Workers: {workers}")
    print(f"   Threads por worker: {threads}")
    print(f"   Capacidad: {workers * threads} requests concurrentes")
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
    print(f"✅ Worker spawneado (PID: {worker.pid})")

def pre_exec(server):
    """Se ejecuta antes de ejecutar el nuevo maestro"""
    print("🔄 Ejecutando nuevo maestro...")

def when_ready(server):
    """Se ejecuta cuando el servidor está listo"""
    pass

def worker_exit(server, worker):
    """Se ejecuta cuando un worker termina"""
    print(f"👋 Worker terminado (PID: {worker.pid})")

def on_exit(server):
    """Se ejecuta cuando el servidor se apaga"""
    print("🛑 Servidor Gunicorn detenido")
