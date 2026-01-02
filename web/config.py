import os

class Config:
    # Flask Config
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_secret_key_para_desarrollo')
    
    # Database Config - Usar variable de entorno
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # Corregir URL de PostgreSQL si viene con postgres://
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    SQLALCHEMY_DATABASE_URI = DATABASE_URL or 'sqlite:///local.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Optimización para PostgreSQL en producción
    if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'pool_recycle': 1800,          # ⬆️ CAMBIO: 280 → 1800
            'pool_size': 10,               # ⬆️ CAMBIO: 5 → 10
            'max_overflow': 15,            # ⬆️ CAMBIO: 10 → 15
            'pool_timeout': 60,            # ⬆️ CAMBIO: 30 → 60
            'connect_args': {
                'connect_timeout': 10,
                'keepalives': 1,
                'keepalives_idle': 30,
                'keepalives_interval': 10,
                'keepalives_count': 5,
            }
        }
    
    # AWS S3 / iDrive e2 Config - Desde variables de entorno
    S3_ENDPOINT = os.environ.get('S3_ENDPOINT')
    S3_KEY = os.environ.get('S3_KEY')
    S3_SECRET = os.environ.get('S3_SECRET')
    S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'taller-computo')
    
    # Upload Config
    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    
    # Cache Config
    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 300