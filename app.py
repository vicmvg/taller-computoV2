import os
import boto3
import qrcode
import magic
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# --- IMPORTS PARA PDF ---
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Helper functions para logging
def log_info(message):
    logger.info(f"✅ {message}")

def log_error(message, error=None):
    msg = f"❌ {message}"
    if error:
        msg += f": {str(error)}"
    logger.error(msg)

def log_warning(message):
    logger.warning(f"⚠️ {message}")

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
app.secret_key = 'clave_secreta_desarrollo'

# Configuración de sesión
app.permanent_session_lifetime = timedelta(minutes=10)

# Configuración de validación de archivos
ALLOWED_EXTENSIONS = {
    'images': {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'},
    'documents': {'pdf', 'doc', 'docx', 'txt', 'odt', 'ppt', 'pptx', 'xls', 'xlsx'},
    'archives': {'zip', 'rar', '7z'}
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# --- CLASES AUXILIARES REFACTORIZADAS ---

class AppError(Exception):
    """Excepción base para errores de la aplicación"""
    pass

class FileValidationError(AppError):
    """Error de validación de archivos"""
    pass

class S3UploadError(AppError):
    """Error al subir archivos a S3"""
    pass

class DiskSpaceError(AppError):
    """Error por espacio insuficiente en disco"""
    pass

class S3Manager:
    """Gestor centralizado para operaciones S3"""
    
    def __init__(self):
        self.endpoint = os.environ.get('S3_ENDPOINT')
        self.key = os.environ.get('S3_KEY')
        self.secret = os.environ.get('S3_SECRET')
        self.bucket = os.environ.get('S3_BUCKET_NAME', 'taller-computo')
        self.is_configured = bool(self.endpoint and self.key and self.secret)
        
        log_info(f"S3 Configurado: {self.is_configured}")
        if self.is_configured:
            log_info(f"Bucket: {self.bucket}")
    
    def get_client(self):
        """Obtener cliente S3 configurado"""
        if not self.is_configured:
            raise S3UploadError("S3 no está configurado")
        
        return boto3.client('s3',
                          endpoint_url=self.endpoint,
                          aws_access_key_id=self.key,
                          aws_secret_access_key=self.secret,
                          region_name='us-west-1')
    
    def upload_file(self, file_stream, key, content_type='application/octet-stream'):
        """Subir archivo a S3"""
        try:
            client = self.get_client()
            file_stream.seek(0)
            client.upload_fileobj(
                file_stream,
                self.bucket,
                key,
                ExtraArgs={'ContentType': content_type}
            )
            log_info(f"Archivo subido a S3: {key}")
            return f"{self.endpoint}/{self.bucket}/{key}"
        except Exception as e:
            log_error(f"Error S3 upload: {str(e)}")
            raise S3UploadError(f"Error al subir a S3: {str(e)}")
    
    def download_file(self, key):
        """Descargar archivo desde S3"""
        try:
            client = self.get_client()
            s3_object = client.get_object(Bucket=self.bucket, Key=key)
            file_content = s3_object['Body'].read()
            content_type = s3_object.get('ContentType', 'application/octet-stream')
            
            log_info(f"Archivo descargado de S3: {key}")
            return BytesIO(file_content), content_type
        except Exception as e:
            log_error(f"Error S3 download: {str(e)}")
            raise S3UploadError(f"Error al descargar de S3: {str(e)}")
    
    def delete_file(self, key):
        """Eliminar archivo de S3"""
        try:
            client = self.get_client()
            client.delete_object(Bucket=self.bucket, Key=key)
            log_info(f"Archivo eliminado de S3: {key}")
        except Exception as e:
            log_error(f"Error S3 delete: {str(e)}")
            raise S3UploadError(f"Error al eliminar de S3: {str(e)}")

class FileValidator:
    """Validador de archivos centralizado"""
    
    def __init__(self):
        self.max_size = MAX_FILE_SIZE
        self.allowed_extensions = set().union(*ALLOWED_EXTENSIONS.values())
        
        # Mapeo de MIME types a extensiones permitidas
        self.mime_to_ext = {
            'image/png': ['png'],
            'image/jpeg': ['jpg', 'jpeg'],
            'image/gif': ['gif'],
            'image/webp': ['webp'],
            'image/bmp': ['bmp'],
            'application/pdf': ['pdf'],
            'application/msword': ['doc'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['docx'],
            'application/vnd.ms-excel': ['xls'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['xlsx'],
            'application/vnd.ms-powerpoint': ['ppt'],
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['pptx'],
            'text/plain': ['txt'],
            'application/zip': ['zip'],
            'application/x-rar-compressed': ['rar'],
            'application/x-7z-compressed': ['7z'],
        }
    
    def validate(self, file_stream, filename):
        """Validación exhaustiva de archivos"""
        # Verificar nombre del archivo
        if '..' in filename or '/' in filename or '\\' in filename:
            raise FileValidationError("Nombre de archivo inválido")
        
        # Verificar tamaño primero (más rápido)
        file_stream.seek(0, 2)
        size = file_stream.tell()
        file_stream.seek(0)
        
        if size > self.max_size:
            raise FileValidationError(f"Archivo demasiado grande (máx {self.max_size/1024/1024}MB)")
        
        # Verificar MIME type (magic number)
        try:
            file_content = file_stream.read(2048)
            file_stream.seek(0)
            
            mime = magic.from_buffer(file_content, mime=True)
            
            if mime not in self.mime_to_ext:
                raise FileValidationError(f"Tipo de archivo {mime} no permitido")
            
            # Verificar extensión
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            if not ext:
                raise FileValidationError("Archivo sin extensión")
            
            # Verificar que extensión coincida con MIME real
            expected_exts = self.mime_to_ext.get(mime, [])
            if ext not in expected_exts and not (mime == 'image/jpeg' and ext in ['jpg', 'jpeg']):
                raise FileValidationError(f"Extensión .{ext} no coincide con tipo real {mime}")
            
            # Verificar contra extensiones permitidas
            if ext not in self.allowed_extensions:
                raise FileValidationError(f"Extensión .{ext} no permitida")
                    
        except Exception as e:
            log_warning(f"Magic number validation skipped: {str(e)}")
            # Si falla la validación MIME, al menos validar extensión
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            if ext not in self.allowed_extensions:
                raise FileValidationError(f"Extensión .{ext} no permitida")
        
        # Validar contenido para archivos de texto
        if ext in ['txt', 'pdf', 'doc', 'docx']:
            try:
                sample = file_stream.read(1024)
                file_stream.seek(0)
                
                if b'\x00' in sample and ext == 'txt':
                    raise FileValidationError("Archivo de texto contiene caracteres binarios")
            except:
                pass
        
        return True

# --- INSTANCIAS GLOBALES ---
s3_manager = S3Manager()
file_validator = FileValidator()

# --- RATE LIMITING SIMPLE ---
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self, max_requests=10, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = defaultdict(list)
    
    def is_allowed(self, key):
        now = time.time()
        window_start = now - self.window_seconds
        
        # Limpiar solicitudes antiguas
        self.requests[key] = [timestamp for timestamp in self.requests[key] if timestamp > window_start]
        
        # Verificar límite
        if len(self.requests[key]) >= self.max_requests:
            return False
        
        # Registrar nueva solicitud
        self.requests[key].append(now)
        return True

# Rate limiter para chat (10 mensajes por minuto)
chat_limiter = RateLimiter(max_requests=10, window_seconds=60)

# --- DECORADORES DE SEGURIDAD OPTIMIZADOS ---

def require_role(role):
    """Decorador genérico para control de acceso por rol"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('tipo_usuario') != role:
                flash(f'Acceso restringido a {role}s', 'danger')
                return redirect(url_for(f'login{"_alumnos" if role == "alumno" else ""}'))
            
            if role == 'profesor' and 'user' not in session:
                flash('Acceso restringido a profesores', 'danger')
                return redirect(url_for('login'))
            
            if role == 'alumno' and 'alumno_id' not in session:
                flash('Debes iniciar sesión como alumno', 'danger')
                return redirect(url_for('login_alumnos'))
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Alias para mantener compatibilidad
require_profesor = require_role('profesor')
require_alumno = require_role('alumno')

def require_any_auth(f):
    """Decorador para rutas que requieren cualquier tipo de autenticación"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'tipo_usuario' not in session:
            flash('Debes iniciar sesión para acceder a esta página', 'danger')
            redirect_to = 'login' if request.path.startswith('/admin') else 'login_alumnos'
            return redirect(url_for(redirect_to))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def make_session_permanent():
    """Marca la sesión como permanente en cada petición"""
    session.permanent = True
    session.modified = True

# --- CONFIGURACIÓN DE BASE DE DATOS ---
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///escuela.db')

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configuración optimizada para Render - CORREGIDA CON SSL
if DATABASE_URL.startswith("postgresql://"):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
        'pool_size': 3,
        'max_overflow': 5,
        'pool_timeout': 30,
        'connect_args': {
            'connect_timeout': 10,
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
            'sslmode': 'require',  # ← AGREGADO: Requerir SSL
            'options': '-c statement_timeout=30000'  # ← AGREGADO: Timeout de consultas
        }
    }
else:
    # Para desarrollo local con SQLite
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 10,
        'max_overflow': 20
    }

db = SQLAlchemy(app)

# Carpeta local de respaldo si no hay S3
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- MODELOS DE LA BASE DE DATOS ---

class Equipo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50))
    marca = db.Column(db.String(50))
    modelo = db.Column(db.String(50))
    estado = db.Column(db.String(20), default='Funcional')
    qr_data = db.Column(db.String(200))

class Mantenimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey('equipo.id'))
    descripcion_falla = db.Column(db.Text)
    fecha_reporte = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_reparacion = db.Column(db.DateTime, nullable=True)
    solucion = db.Column(db.Text, nullable=True)
    equipo = db.relationship('Equipo', backref=db.backref('mantenimientos', lazy=True))

class Anuncio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100))
    contenido = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class UsuarioAlumno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    nombre_completo = db.Column(db.String(100), nullable=False)
    grado_grupo = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    activo = db.Column(db.Boolean, default=True)
    foto_perfil = db.Column(db.String(300), nullable=True, default=None)
    
    # RELACIONES CON back_populates (CORREGIDO - sin duplicados)
    entregas = db.relationship('EntregaAlumno', back_populates='alumno', cascade='all, delete-orphan')
    asistencias = db.relationship('Asistencia', back_populates='alumno', cascade='all, delete-orphan')
    boletas = db.relationship('BoletaGenerada', back_populates='alumno', cascade='all, delete-orphan')
    pagos = db.relationship('Pago', back_populates='alumno', cascade='all, delete-orphan')

class EntregaAlumno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'), nullable=False)
    nombre_alumno = db.Column(db.String(100))
    grado_grupo = db.Column(db.String(20))
    titulo_tarea = db.Column(db.String(200))
    archivo_url = db.Column(db.String(300))
    estrellas = db.Column(db.Integer, default=0)
    comentarios = db.Column(db.Text)
    fecha_entrega = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relación con alumno usando back_populates
    alumno = db.relationship('UsuarioAlumno', back_populates='entregas')

class Asistencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    fecha = db.Column(db.Date, default=datetime.utcnow)
    estado = db.Column(db.String(10))
    
    # Relación con alumno usando back_populates
    alumno = db.relationship('UsuarioAlumno', back_populates='asistencias')

class ReporteAsistencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grupo = db.Column(db.String(20), nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date, nullable=True)
    fecha_generacion = db.Column(db.DateTime, default=datetime.utcnow)
    archivo_url = db.Column(db.String(500))
    nombre_archivo = db.Column(db.String(200))
    generado_por = db.Column(db.String(100))
    total_alumnos = db.Column(db.Integer, default=0)
    total_registros = db.Column(db.Integer, default=0)

class ActividadGrado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grado = db.Column(db.Integer)
    titulo = db.Column(db.String(100))
    descripcion = db.Column(db.Text)
    imagen_url = db.Column(db.String(200), nullable=True)
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow)

class Cuestionario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100))
    url = db.Column(db.String(500))
    grado = db.Column(db.String(20))
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class BancoCuestionario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)

class Horario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dia = db.Column(db.String(20))
    grados = db.Column(db.String(50))
    hora = db.Column(db.String(50))

class Plataforma(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50))
    url = db.Column(db.String(500))
    icono = db.Column(db.String(50))

class Mensaje(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'))
    nombre_alumno = db.Column(db.String(100))
    grado_grupo = db.Column(db.String(20))
    contenido = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class MensajeFlotante(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grado_grupo = db.Column(db.String(20), nullable=False)
    contenido = db.Column(db.Text, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    activo = db.Column(db.Boolean, default=True)
    creado_por = db.Column(db.String(100))

class MensajeLeido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mensaje_id = db.Column(db.Integer, db.ForeignKey('mensaje_flotante.id'))
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'))
    fecha_lectura = db.Column(db.DateTime, default=datetime.utcnow)

class Configuracion(db.Model):
    clave = db.Column(db.String(50), primary_key=True)
    valor = db.Column(db.String(200))

class Recurso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100), nullable=False)
    archivo_url = db.Column(db.String(300), nullable=False)
    tipo_archivo = db.Column(db.String(10))
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class CriterioBoleta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grado = db.Column(db.String(10))
    nombre = db.Column(db.String(100))

class BoletaGenerada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    archivo_url = db.Column(db.String(500))
    nombre_archivo = db.Column(db.String(200))
    fecha_generacion = db.Column(db.DateTime, default=datetime.utcnow)
    periodo = db.Column(db.String(50))
    promedio = db.Column(db.Float)
    observaciones = db.Column(db.Text)
    generado_por = db.Column(db.String(100))
    
    # Relación con alumno usando back_populates
    alumno = db.relationship('UsuarioAlumno', back_populates='boletas')

class Pago(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    monto_total = db.Column(db.Float, nullable=False)
    monto_pagado = db.Column(db.Float, default=0)
    monto_pendiente = db.Column(db.Float)
    tipo_pago = db.Column(db.String(20))
    estado = db.Column(db.String(20), default='pendiente')
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_vencimiento = db.Column(db.Date, nullable=True)
    grado_grupo = db.Column(db.String(20))
    creado_por = db.Column(db.String(100))
    recibos = db.relationship('ReciboPago', backref='pago', lazy=True, cascade='all, delete-orphan')
    
    # Relación con alumno usando back_populates
    alumno = db.relationship('UsuarioAlumno', back_populates='pagos')

class ReciboPago(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pago_id = db.Column(db.Integer, db.ForeignKey('pago.id', ondelete='CASCADE'), nullable=False)
    numero_recibo = db.Column(db.String(50), unique=True, nullable=False)
    monto = db.Column(db.Float, nullable=False)
    metodo_pago = db.Column(db.String(50))
    archivo_url = db.Column(db.String(500))
    nombre_archivo = db.Column(db.String(200))
    fecha_pago = db.Column(db.DateTime, default=datetime.utcnow)
    recibido_por = db.Column(db.String(100))
    observaciones = db.Column(db.Text)

# --- NUEVOS MODELOS PARA SISTEMA DE ARCHIVOS ---

class SolicitudArchivo(db.Model):
    """Solicitudes de archivos que los alumnos hacen al profesor"""
    __tablename__ = 'solicitudes_archivo'
    
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    tipo_documento = db.Column(db.String(100), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='pendiente')
    fecha_solicitud = db.Column(db.DateTime, default=datetime.now)
    fecha_respuesta = db.Column(db.DateTime, nullable=True)
    
    # Relación con alumno usando backref (se mantiene aquí)
    alumno = db.relationship('UsuarioAlumno', backref='solicitudes_archivos')

class ArchivoEnviado(db.Model):
    """Archivos PDF que el profesor envía a los alumnos"""
    __tablename__ = 'archivos_enviados'
    
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    solicitud_id = db.Column(db.Integer, db.ForeignKey('solicitudes_archivo.id', ondelete='SET NULL'), nullable=True)
    
    titulo = db.Column(db.String(200), nullable=False)
    mensaje = db.Column(db.Text, nullable=True)
    
    archivo_url = db.Column(db.String(500), nullable=False)
    nombre_archivo = db.Column(db.String(200), nullable=False)
    
    leido = db.Column(db.Boolean, default=False)
    fecha_envio = db.Column(db.DateTime, default=datetime.now)
    fecha_lectura = db.Column(db.DateTime, nullable=True)
    
    enviado_por = db.Column(db.String(100), nullable=False)
    
    # Relaciones usando backref (se mantienen aquí)
    alumno = db.relationship('UsuarioAlumno', backref='archivos_recibidos')
    solicitud = db.relationship('SolicitudArchivo', backref='archivo_respuesta', uselist=False)

# --- FUNCIONES AUXILIARES OPTIMIZADAS ---

def get_current_user():
    """Retorna (tipo_usuario, id, datos) o None"""
    if 'tipo_usuario' not in session:
        return None
    
    if session['tipo_usuario'] == 'profesor':
        return ('profesor', session.get('user'), {'username': session.get('user')})
    elif session['tipo_usuario'] == 'alumno':
        return ('alumno', session.get('alumno_id'), {
            'id': session.get('alumno_id'),
            'nombre': session.get('alumno_nombre'),
            'grado': session.get('alumno_grado'),
            'username': session.get('alumno_username')
        })
    return None

def column_exists(table_name, column_name):
    """Verifica si una columna existe en una tabla"""
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)

def migrar_bd():
    """Ejecutar todas las migraciones necesarias"""
    migraciones = [
        ('usuario_alumno', 'foto_perfil', 'VARCHAR(300)'),
        ('entrega_alumno', 'titulo_tarea', 'VARCHAR(200)')
    ]
    
    allowed_tables = {'usuario_alumno', 'entrega_alumno'}
    allowed_columns = {'foto_perfil', 'titulo_tarea'}
    
    for tabla, columna, tipo in migraciones:
        if tabla not in allowed_tables or columna not in allowed_columns:
            log_error(f"Intento de migración con nombres no permitidos: {tabla}.{columna}")
            continue
            
        if not column_exists(tabla, columna):
            log_info(f"Agregando columna '{columna}' a '{tabla}'...")
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))
                    conn.commit()
            except Exception as e:
                log_error(f"Error al agregar columna {columna} a {tabla}: {str(e)}")
    
    if not column_exists('solicitudes_archivo', 'id'):
        log_info("Creando tabla solicitudes_archivo...")
        db.create_all()
    
    if not column_exists('archivos_enviados', 'id'):
        log_info("Creando tabla archivos_enviados...")
        db.create_all()
    
    migrar_bd_pagos()

def migrar_bd_pagos():
    """Migración para crear tablas de pagos si no existen"""
    try:
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tablas_existentes = inspector.get_table_names()
        
        if 'pago' not in tablas_existentes or 'recibo_pago' not in tablas_existentes:
            log_info("Creando tablas de pagos...")
            db.create_all()
            log_info("Tablas de pagos creadas correctamente")
        else:
            log_info("Tablas de pagos ya existen")
    except Exception as e:
        log_error(f"Error en migración de pagos: {str(e)}")

def check_disk_space(min_free_gb=1):
    """Verifica que haya espacio suficiente en disco"""
    try:
        import shutil
        stat = shutil.disk_usage(UPLOAD_FOLDER)
        free_gb = stat.free / (1024**3)
        if free_gb < min_free_gb:
            raise DiskSpaceError(f"Espacio insuficiente en disco. Solo {free_gb:.2f}GB disponibles (mínimo {min_free_gb}GB requerido)")
        return True
    except Exception as e:
        log_warning(f"No se pudo verificar espacio en disco: {str(e)}")
        return True

def generar_recibo_pdf(recibo, alumno, pago):
    """Genera un recibo de pago en PDF con formato profesional"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    elementos = []
    styles = getSampleStyleSheet()
    
    titulo_style = ParagraphStyle(
        'TituloRecibo',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1e293b'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    elementos.append(Paragraph("RECIBO DE PAGO", titulo_style))
    elementos.append(Spacer(1, 0.3*inch))
    
    info_recibo = [
        ['Número de Recibo:', recibo.numero_recibo, 'Fecha:', recibo.fecha_pago.strftime('%d/%m/%Y')],
        ['', '', 'Hora:', recibo.fecha_pago.strftime('%H:%M:%S')]
    ]
    
    tabla_info = Table(info_recibo, colWidths=[1.5*inch, 2.5*inch, 1*inch, 2*inch])
    tabla_info.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#475569')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    
    elementos.append(tabla_info)
    elementos.append(Spacer(1, 0.3*inch))
    
    elementos.append(Table([['']], colWidths=[7*inch], 
                          style=[('LINEABOVE', (0,0), (-1,0), 2, colors.HexColor('#3b82f6'))]))
    elementos.append(Spacer(1, 0.2*inch))
    
    datos_alumno = [
        ['DATOS DEL ALUMNO', ''],
        ['Nombre:', alumno.nombre_completo],
        ['Grado y Grupo:', alumno.grado_grupo],
        ['Matrícula:', alumno.username]
    ]
    
    tabla_alumno = Table(datos_alumno, colWidths=[2*inch, 5*inch])
    tabla_alumno.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('PADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
    ]))
    
    elementos.append(tabla_alumno)
    elementos.append(Spacer(1, 0.3*inch))
    
    datos_pago = [
        ['DETALLE DEL PAGO', '', ''],
        ['Concepto:', pago.concepto, ''],
        ['Monto Total:', f'${pago.monto_total:,.2f}', ''],
        ['Monto Pagado:', f'${recibo.monto:,.2f}', ''],
    ]
    
    if pago.tipo_pago == 'diferido':
        nuevo_pendiente = pago.monto_pendiente - recibo.monto if pago.monto_pendiente else pago.monto_total - recibo.monto
        datos_pago.append(['Saldo Pendiente:', f'${nuevo_pendiente:,.2f}', ''])
    
    datos_pago.extend([
        ['Método de Pago:', recibo.metodo_pago.capitalize(), ''],
        ['Recibido por:', recibo.recibido_por, '']
    ])
    
    tabla_pago = Table(datos_pago, colWidths=[2*inch, 3*inch, 2*inch])
    tabla_pago.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('SPAN', (0, 0), (-1, 0)),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('PADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor('#fef3c7')),
    ]))
    
    elementos.append(tabla_pago)
    elementos.append(Spacer(1, 0.4*inch))
    
    if recibo.observaciones:
        elementos.append(Paragraph(f"<b>Observaciones:</b> {recibo.observaciones}", styles['Normal']))
        elementos.append(Spacer(1, 0.3*inch))
    
    elementos.append(Spacer(1, 0.5*inch))
    firma = Table([['_' * 50]], colWidths=[3.5*inch])
    firma.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
    elementos.append(firma)
    elementos.append(Paragraph("Firma y Sello", styles['Normal']))
    
    elementos.append(Spacer(1, 0.5*inch))
    footer_text = f"<i>Recibo generado electrónicamente el {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>"
    elementos.append(Paragraph(footer_text, styles['Italic']))
    
    doc.build(elementos)
    buffer.seek(0)
    return buffer

def guardar_archivo(archivo):
    """Guarda archivo en S3 o localmente con verificación de espacio"""
    filename = secure_filename(archivo.filename)
    
    log_info(f"Intentando guardar archivo: {filename}")
    
    try:
        file_validator.validate(archivo, filename)
        archivo.seek(0)
        
        if s3_manager.is_configured:
            try:
                content_type = archivo.content_type or 'application/octet-stream'
                s3_key = f"uploads/{filename}"
                s3_manager.upload_file(archivo, s3_key, content_type)
                return (s3_key, True)
            except S3UploadError as e:
                log_warning(f"Error S3: {e}. Verificando espacio en disco...")
                if not check_disk_space():
                    raise DiskSpaceError("No hay espacio suficiente en disco para guardar el archivo localmente")
                flash('Advertencia: No se pudo subir a la nube. Guardado localmente.', 'warning')
        
        if not check_disk_space():
            raise DiskSpaceError("No hay espacio suficiente en disco")
        
        local_path = os.path.join(UPLOAD_FOLDER, filename)
        archivo.save(local_path)
        log_info(f"Archivo guardado localmente: {filename}")
        
        return (filename, False)
        
    except (FileValidationError, DiskSpaceError) as e:
        raise e
    except Exception as e:
        raise AppError(f"Error al guardar archivo: {str(e)}")

def descargar_archivo(archivo_url, nombre_archivo, carpeta_local):
    """Función helper para descargar archivos desde S3 o local"""
    if archivo_url and (archivo_url.startswith('http') or 'uploads/' in archivo_url):
        if s3_manager.is_configured:
            file_stream, content_type = s3_manager.download_file(archivo_url)
            return send_file(file_stream, mimetype=content_type, 
                           as_attachment=True, download_name=nombre_archivo)
    
    return send_from_directory(os.path.join(UPLOAD_FOLDER, carpeta_local), 
                              nombre_archivo, as_attachment=True)

def generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin=None):
    """Genera un PDF con el reporte de asistencia optimizado para N+1"""
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a5490'),
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        titulo = f"Reporte de Asistencia - Grupo {grupo}"
        elements.append(Paragraph(titulo, title_style))
        elements.append(Spacer(1, 12))
        
        periodo = f"Período: {fecha_inicio} a {fecha_fin}" if fecha_fin else f"Fecha: {fecha_inicio}"
        info_style = ParagraphStyle(
            'Info',
            parent=styles['Normal'],
            fontSize=12,
            spaceAfter=20,
            alignment=TA_CENTER
        )
        elements.append(Paragraph(periodo, info_style))
        elements.append(Paragraph(f"Generado el: {datetime.now().strftime('%d/%m/%Y %H:%M')}", info_style))
        elements.append(Spacer(1, 20))
        
        fecha_inicio_obj = datetime.strptime(fecha_inicio, '%Y-%m-%d').date() if isinstance(fecha_inicio, str) else fecha_inicio
        
        from sqlalchemy import func, case
        
        # CORRECCIÓN: Cambiar case([(condición, valor)]) por case((condición, valor))
        alumnos_con_stats = db.session.query(
            UsuarioAlumno.id,
            UsuarioAlumno.nombre_completo,
            func.count(case((Asistencia.estado == 'P', 1))).label('presentes'),
            func.count(case((Asistencia.estado == 'F', 1))).label('faltas'),
            func.count(case((Asistencia.estado == 'R', 1))).label('retardos'),
            func.count(case((Asistencia.estado == 'J', 1))).label('justificados'),
            func.count(Asistencia.id).label('total')
        ).outerjoin(
            Asistencia,
            (Asistencia.alumno_id == UsuarioAlumno.id) & 
            (Asistencia.fecha >= fecha_inicio_obj) &
            (Asistencia.fecha <= (datetime.strptime(fecha_fin, '%Y-%m-%d').date() if fecha_fin else fecha_inicio_obj))
        ).filter(
            UsuarioAlumno.grado_grupo == grupo
        ).group_by(
            UsuarioAlumno.id,
            UsuarioAlumno.nombre_completo
        ).order_by(
            UsuarioAlumno.nombre_completo
        ).all()
        
        data = [['#', 'Nombre del Alumno', 'Presente', 'Falta', 'Retardo', 'Justificado', 'Total']]
        
        for idx, alumno in enumerate(alumnos_con_stats, 1):
            data.append([
                str(idx),
                alumno.nombre_completo,
                str(alumno.presentes or 0),
                str(alumno.faltas or 0),
                str(alumno.retardos or 0),
                str(alumno.justificados or 0),
                str(alumno.total or 0)
            ])
        
        tabla = Table(data, colWidths=[0.5*inch, 3*inch, 0.8*inch, 0.8*inch, 0.8*inch, 1*inch, 1*inch])
        
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5490')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        
        elements.append(tabla)
        elements.append(Spacer(1, 30))
        
        total_alumnos = len(alumnos_con_stats)
        total_registros = sum(alumno.total or 0 for alumno in alumnos_con_stats)
        
        stats_text = f"""<b>Resumen del Grupo:</b><br/>
        Total de alumnos: {total_alumnos}<br/>
        Total de registros de asistencia: {total_registros}"""
        
        stats_style = ParagraphStyle('Stats', parent=styles['Normal'], fontSize=11, spaceAfter=20)
        elements.append(Paragraph(stats_text, stats_style))
        
        doc.build(elements)
        buffer.seek(0)
        
        fecha_str = fecha_inicio_obj.strftime('%Y%m%d')
        if fecha_fin:
            fecha_fin_str = datetime.strptime(fecha_fin, '%Y-%m-%d').strftime('%Y%m%d') if isinstance(fecha_fin, str) else fecha_fin.strftime('%Y%m%d')
            filename = f"asistencia_{grupo}_{fecha_str}_a_{fecha_fin_str}.pdf"
        else:
            filename = f"asistencia_{grupo}_{fecha_str}.pdf"
        
        file_url = None
        if s3_manager.is_configured:
            try:
                buffer_copy = BytesIO(buffer.getvalue())
                s3_key = f"reportes/{filename}"
                file_url = s3_manager.upload_file(buffer_copy, s3_key, 'application/pdf')
            except S3UploadError as e:
                log_warning(f"No se pudo guardar en S3: {str(e)}")
        
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'reportes'), exist_ok=True)
        local_path = os.path.join(UPLOAD_FOLDER, 'reportes', filename)
        
        with open(local_path, 'wb') as f:
            f.write(buffer.getvalue())
        
        nuevo_reporte = ReporteAsistencia(
            grupo=grupo,
            fecha_inicio=fecha_inicio_obj,
            fecha_fin=datetime.strptime(fecha_fin, '%Y-%m-%d').date() if fecha_fin else None,
            archivo_url=file_url or f"reportes/{filename}",
            nombre_archivo=filename,
            generado_por=session.get('user', 'Sistema'),
            total_alumnos=total_alumnos,
            total_registros=total_registros
        )
        
        db.session.add(nuevo_reporte)
        db.session.commit()
        
        buffer.seek(0)
        return (file_url, buffer, filename)
        
    except Exception as e:
        log_error(f"Error al generar PDF de asistencia: {str(e)}")
        raise AppError(f"Error al generar reporte: {str(e)}")

def generar_pdf_boleta(alumno, datos_evaluacion, observaciones, promedio, periodo):
    """Genera PDF de boleta y guarda en S3"""
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                     fontSize=20, textColor=colors.HexColor('#1a5490'), 
                                     spaceAfter=20, alignment=TA_CENTER)
        
        elements.append(Paragraph("ESCUELA MARIANO ESCOBEDO", title_style))
        elements.append(Paragraph("Boleta de Calificaciones", styles['Heading2']))
        elements.append(Spacer(1, 20))
        
        info = f"<b>Alumno:</b> {alumno.nombre_completo}<br/><b>Grado:</b> {alumno.grado_grupo}<br/><b>Período:</b> {periodo}<br/><b>Fecha:</b> {datetime.now().strftime('%d/%m/%Y')}"
        elements.append(Paragraph(info, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        data = [['Criterio de Evaluación', 'Calificación']]
        for criterio, nota in datos_evaluacion.items():
            data.append([criterio.replace('_', ' ').title(), str(nota)])
        data.append(['', ''])
        data.append([Paragraph('<b>PROMEDIO GENERAL</b>', styles['Normal']), 
                     Paragraph(f'<b>{promedio}</b>', styles['Normal'])])
        
        tabla = Table(data, colWidths=[4*inch, 2*inch])
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5490')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -2), 1, colors.black),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e8f4f8')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        elements.append(tabla)
        elements.append(Spacer(1, 30))
        
        if observaciones:
            elements.append(Paragraph("<b>Observaciones:</b>", styles['Heading3']))
            elements.append(Paragraph(observaciones, styles['Normal']))
            elements.append(Spacer(1, 20))
        
        elements.append(Spacer(1, 40))
        firma_tabla = Table([['_________________________', '_________________________'],
                            ['Firma del Profesor', 'Firma del Padre/Tutor']], 
                           colWidths=[3*inch, 3*inch])
        firma_tabla.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTSIZE', (0, 0), (-1, -1), 10)]))
        elements.append(firma_tabla)
        
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"boleta_{alumno.grado_grupo}_{alumno.nombre_completo.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_url = None
        
        if s3_manager.is_configured:
            try:
                s3_key = f"boletas/{filename}"
                file_url = s3_manager.upload_file(BytesIO(buffer.getvalue()), s3_key, 'application/pdf')
            except S3UploadError as e:
                log_warning(f"Error S3: {e}")
        
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'boletas'), exist_ok=True)
        with open(os.path.join(UPLOAD_FOLDER, 'boletas', filename), 'wb') as f:
            f.write(buffer.getvalue())
        
        buffer.seek(0)
        return (file_url or f"boletas/{filename}", buffer, filename)
        
    except Exception as e:
        log_error(f"Error al generar PDF de boleta: {str(e)}")
        raise AppError(f"Error al generar boleta: {str(e)}")

# --- HANDLERS DE ERROR ---

@app.errorhandler(404)
def not_found_error(error):
    log_warning(f"404 Error: {request.url}")
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    log_error(f"500 Error: {str(error)}")
    return render_template('errors/500.html'), 500

@app.errorhandler(AppError)
def app_error(error):
    log_error(f"AppError: {str(error)}")
    flash(f'Error en la aplicación: {str(error)}', 'danger')
    return redirect(url_for('index'))

@app.errorhandler(FileValidationError)
def file_validation_error(error):
    log_warning(f"FileValidationError: {str(error)}")
    flash(f'Error de validación de archivo: {str(error)}', 'danger')
    return redirect(request.referrer or url_for('index'))

@app.errorhandler(S3UploadError)
def s3_upload_error(error):
    log_error(f"S3UploadError: {str(error)}")
    flash(f'Error al subir a la nube: {str(error)}', 'danger')
    return redirect(request.referrer or url_for('index'))

@app.errorhandler(DiskSpaceError)
def disk_space_error(error):
    log_error(f"DiskSpaceError: {str(error)}")
    flash(f'Error de almacenamiento: {str(error)}', 'danger')
    return redirect(request.referrer or url_for('index'))

# --- RUTAS PRINCIPALES ---

@app.route('/')
def index():
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(5).all()
    horarios = Horario.query.all()
    plataformas = Plataforma.query.all()
    recursos = Recurso.query.order_by(Recurso.fecha.desc()).all()

    return render_template('index.html', anuncios=anuncios, horarios=horarios, plataformas=plataformas, recursos=recursos)

# --- RUTAS DE AUTENTICACIÓN ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    user_info = get_current_user()
    if user_info and user_info[0] == 'profesor':
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username == 'admin':
            config_pass = Configuracion.query.get('admin_password')
            
            if config_pass:
                es_valida = check_password_hash(config_pass.valor, password)
            else:
                es_valida = (password == 'profesor123')

            if es_valida:
                session.permanent = True
                session['user'] = username
                session['tipo_usuario'] = 'profesor'
                flash('¡Bienvenido, Profesor!', 'success')
                return redirect(url_for('admin_dashboard'))
            else:
                flash('Contraseña incorrecta.', 'danger')
        else:
            flash('Usuario incorrecto.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente.')
    return redirect(url_for('index'))

@app.route('/recuperar-acceso', methods=['GET', 'POST'])
def recuperar_acceso():
    if request.method == 'POST':
        usuario = request.form['usuario']
        token = request.form['token']
        nueva_pass = request.form['nueva_pass']
        
        if usuario == 'admin' and token == "treceT1gres":
            hash_pass = generate_password_hash(nueva_pass)
            
            config = Configuracion.query.get('admin_password')
            if not config:
                config = Configuracion(clave='admin_password', valor=hash_pass)
                db.session.add(config)
            else:
                config.valor = hash_pass
            
            db.session.commit()
            flash('¡Contraseña restablecida con éxito! Inicia sesión ahora.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Token maestro incorrecto o usuario no válido.', 'danger')
            
    return render_template('recuperar.html')

@app.route('/alumnos/login', methods=['GET', 'POST'])
def login_alumnos():
    user_info = get_current_user()
    if user_info and user_info[0] == 'alumno':
        return redirect(url_for('panel_alumnos'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        alumno = UsuarioAlumno.query.filter_by(username=username, activo=True).first()
        
        if alumno and check_password_hash(alumno.password_hash, password):
            session.permanent = True
            session['alumno_id'] = alumno.id
            session['alumno_nombre'] = alumno.nombre_completo
            session['alumno_grado'] = alumno.grado_grupo
            session['alumno_username'] = alumno.username
            session['tipo_usuario'] = 'alumno'
            
            flash(f'¡Bienvenido {alumno.nombre_completo}!', 'success')
            return redirect(url_for('panel_alumnos'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
            return redirect(url_for('login_alumnos'))
        
    return render_template('alumnos/login.html')

@app.route('/alumnos/logout')
def logout_alumnos():
    session.clear()
    return redirect(url_for('index'))

@app.route('/alumnos/perfil/foto', methods=['POST'])
@require_alumno
def actualizar_foto_perfil():
    if 'foto' not in request.files:
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    foto = request.files['foto']
    
    if foto.filename == '':
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    ext = foto.filename.rsplit('.', 1)[1].lower() if '.' in foto.filename else ''
    if ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']:
        flash('⚠️ Solo se permiten archivos de imagen (PNG, JPG, GIF, WEBP)', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    try:
        ruta_foto, es_s3 = guardar_archivo(foto)
        alumno = UsuarioAlumno.query.get(session['alumno_id'])
        alumno.foto_perfil = ruta_foto
        db.session.commit()
        
        flash('¡Foto de perfil actualizada correctamente! 🎉', 'success')
        
    except (FileValidationError, AppError, DiskSpaceError) as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('panel_alumnos'))

# --- RUTAS DE ADMINISTRACIÓN ---

@app.route('/admin')
@require_profesor
def admin_dashboard():
    equipos = Equipo.query.count()
    pendientes = Mantenimiento.query.filter_by(fecha_reparacion=None).count()
    alumnos_activos = UsuarioAlumno.query.filter_by(activo=True).count()
    total_entregas = EntregaAlumno.query.count()
    
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False
    
    return render_template('admin/dashboard.html', 
                         total_equipos=equipos, 
                         reparaciones=pendientes,
                         alumnos_activos=alumnos_activos,
                         total_entregas=total_entregas,
                         chat_activo=chat_activo)

# --- RUTAS DEL CHAT ---

@app.route('/admin/chat/toggle')
@require_profesor
def toggle_chat():
    config = Configuracion.query.get('chat_activo')
    if not config:
        config = Configuracion(clave='chat_activo', valor='True')
        db.session.add(config)
    
    if config.valor == 'True':
        config.valor = 'False'
        flash('Chat desactivado para todos los alumnos.', 'secondary')
    else:
        config.valor = 'True'
        flash('Chat activado. Los alumnos pueden conversar.', 'success')
    
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/api/chat/enviar', methods=['POST'])
@require_alumno
def enviar_mensaje():
    alumno_key = f"alumno_{session['alumno_id']}"
    if not chat_limiter.is_allowed(alumno_key):
        return {'status': 'error', 'msg': 'Demasiados mensajes. Espera un momento.'}, 429
    
    config = Configuracion.query.get('chat_activo')
    if config and config.valor == 'False':
        return {'status': 'error', 'msg': 'Chat desactivado por el profesor'}, 403

    contenido = request.form.get('mensaje')
    if not contenido or contenido.strip() == '':
        return {'status': 'error', 'msg': 'Mensaje vacío'}, 400

    nuevo = Mensaje(
        alumno_id=session['alumno_id'],
        nombre_alumno=session['alumno_nombre'],
        grado_grupo=session['alumno_grado'],
        contenido=contenido
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    return {'status': 'ok'}

@app.route('/api/chat/obtener')
@require_alumno
def obtener_mensajes():
    mi_grupo = session['alumno_grado']
    mensajes = Mensaje.query.filter_by(grado_grupo=mi_grupo).order_by(Mensaje.fecha.asc()).all()
    
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False

    lista_mensajes = []
    for m in mensajes:
        es_mio = (m.alumno_id == session['alumno_id'])
        lista_mensajes.append({
            'nombre': 'Yo' if es_mio else m.nombre_alumno,
            'texto': m.contenido,
            'es_mio': es_mio,
            'hora': m.fecha.strftime('%H:%M')
        })

    return {
        'mensajes': lista_mensajes,
        'activo': chat_activo
    }

# --- RUTAS DE GESTIÓN DE ALUMNOS Y ASISTENCIA ---

@app.route('/admin/alumnos')
@require_profesor
def gestionar_alumnos():
    filtro = request.args.get('grado')
    
    if filtro and filtro != 'Todos':
        alumnos = UsuarioAlumno.query.filter_by(grado_grupo=filtro).order_by(UsuarioAlumno.nombre_completo).all()
    else:
        alumnos = UsuarioAlumno.query.order_by(UsuarioAlumno.grado_grupo, UsuarioAlumno.nombre_completo).all()
    
    total_alumnos = UsuarioAlumno.query.count()
    alumnos_activos = UsuarioAlumno.query.filter_by(activo=True).count()
    
    return render_template('admin/alumnos.html', 
                         alumnos=alumnos, 
                         total_alumnos=total_alumnos,
                         alumnos_activos=alumnos_activos,
                         filtro_actual=filtro,
                         fecha_hoy=datetime.now().date().isoformat())

@app.route('/admin/alumnos/agregar', methods=['POST'])
@require_profesor
def agregar_alumno():
    username = request.form['username']
    nombre_completo = request.form['nombre_completo']
    password = request.form['password']
    grado = request.form['grado']
    grupo = request.form['grupo']
    grado_grupo = f"{grado}{grupo}"
    
    existe = UsuarioAlumno.query.filter_by(username=username).first()
    if existe:
        flash(f'El usuario "{username}" ya existe. Elige otro.', 'danger')
        return redirect(url_for('gestionar_alumnos'))
    
    nuevo_alumno = UsuarioAlumno(
        username=username,
        nombre_completo=nombre_completo,
        grado_grupo=grado_grupo,
        password_hash=generate_password_hash(password),
        activo=True
    )
    
    db.session.add(nuevo_alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre_completo} inscrito en {grado_grupo}.', 'success')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/editar/<int:id>', methods=['POST'])
@require_profesor
def editar_alumno(id):
    alumno = UsuarioAlumno.query.get_or_404(id)
    
    alumno.nombre_completo = request.form['nombre_completo']
    alumno.grado_grupo = request.form['grado_grupo']
    alumno.activo = 'activo' in request.form
    
    nueva_password = request.form.get('password')
    if nueva_password:
        alumno.password_hash = generate_password_hash(nueva_password)
    
    db.session.commit()
    
    flash(f'Datos de {alumno.nombre_completo} actualizados.', 'success')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/eliminar/<int:id>')
@require_profesor
def eliminar_alumno(id):
    alumno = UsuarioAlumno.query.get_or_404(id)
    nombre = alumno.nombre_completo
    
    db.session.delete(alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre} eliminado del sistema.', 'warning')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/asistencia/tomar', methods=['POST'])
@require_profesor
def tomar_asistencia():
    fecha_str = request.form.get('fecha', datetime.utcnow().strftime('%Y-%m-%d'))
    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    
    for key, value in request.form.items():
        if key.startswith('asistencia_'):
            alumno_id = int(key.split('_')[1])
            estado = value
            
            registro = Asistencia.query.filter_by(alumno_id=alumno_id, fecha=fecha_obj).first()
            
            if registro:
                registro.estado = estado
            else:
                nuevo = Asistencia(alumno_id=alumno_id, fecha=fecha_obj, estado=estado)
                db.session.add(nuevo)
    
    db.session.commit()
    flash(f'Asistencia del día {fecha_str} guardada correctamente.', 'success')
    return redirect(url_for('gestionar_alumnos', grado=request.form.get('grado_origen')))

@app.route('/admin/reporte-asistencia/<grupo>')
@require_profesor
def generar_reporte_asistencia(grupo):
    fecha_inicio = request.args.get('fecha_inicio', datetime.now().date().isoformat())
    fecha_fin = request.args.get('fecha_fin', None)
    
    try:
        url_guardado, buffer_pdf, nombre_archivo = generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin)
        
        if url_guardado:
            flash('✅ Reporte generado y guardado automáticamente en iDrive e2', 'success')
        else:
            flash('✅ Reporte generado correctamente', 'success')
        
        return send_file(
            buffer_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=nombre_archivo
        )
        
    except AppError as e:
        log_error(f"Error al generar reporte: {str(e)}")
        flash(f'❌ Error al generar reporte: {str(e)}', 'danger')
        return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/descargar-reporte/<path:filename>')
@require_profesor
def descargar_reporte(filename):
    try:
        return send_from_directory(
            os.path.join(UPLOAD_FOLDER, 'reportes'),
            filename,
            as_attachment=True
        )
    except Exception as e:
        log_error(f"Error al descargar reporte: {str(e)}")
        flash(f'Error al descargar reporte: {str(e)}', 'danger')
        return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/entregas')
@require_profesor
def ver_entregas_alumnos():
    entregas = EntregaAlumno.query.order_by(EntregaAlumno.fecha_entrega.desc()).all()
    
    entregas_por_alumno = {}
    for entrega in entregas:
        if entrega.nombre_alumno not in entregas_por_alumno:
            entregas_por_alumno[entrega.nombre_alumno] = []
        entregas_por_alumno[entrega.nombre_alumno].append(entrega)
    
    return render_template('admin/entregas_alumnos.html', 
                         entregas=entregas,
                         entregas_por_alumno=entregas_por_alumno)

@app.route('/admin/alumnos/calificar/<int:id>', methods=['POST'])
@require_profesor
def calificar_entrega(id):
    entrega = EntregaAlumno.query.get_or_404(id)
    entrega.estrellas = int(request.form['estrellas'])
    entrega.comentarios = request.form['comentarios']
    
    db.session.commit()
    
    flash(f'Entrega de {entrega.nombre_alumno} calificada con {entrega.estrellas} estrellas.', 'success')
    return redirect(url_for('ver_entregas_alumnos'))

@app.route('/admin/reportes-asistencia')
@require_profesor
def ver_reportes_asistencia():
    filtro_grupo = request.args.get('grupo', 'Todos')
    filtro_mes = request.args.get('mes', '')
    filtro_anio = request.args.get('anio', '')
    
    query = ReporteAsistencia.query
    
    if filtro_grupo and filtro_grupo != 'Todos':
        query = query.filter_by(grupo=filtro_grupo)
    
    if filtro_mes and filtro_anio:
        try:
            mes = int(filtro_mes)
            anio = int(filtro_anio)
            primer_dia = datetime(anio, mes, 1).date()
            if mes == 12:
                ultimo_dia = datetime(anio + 1, 1, 1).date() - timedelta(days=1)
            else:
                ultimo_dia = datetime(anio, mes + 1, 1).date() - timedelta(days=1)
            
            query = query.filter(
                ReporteAsistencia.fecha_inicio >= primer_dia,
                ReporteAsistencia.fecha_inicio <= ultimo_dia
            )
        except:
            pass
    
    reportes = query.order_by(ReporteAsistencia.fecha_generacion.desc()).all()
    
    grupos_disponibles = db.session.query(ReporteAsistencia.grupo).distinct().all()
    grupos_disponibles = [g[0] for g in grupos_disponibles]
    
    total_reportes = ReporteAsistencia.query.count()
    reportes_este_mes = ReporteAsistencia.query.filter(
        ReporteAsistencia.fecha_generacion >= datetime.now().date().replace(day=1)
    ).count()
    
    return render_template('admin/reportes_asistencia.html',
                         reportes=reportes,
                         grupos_disponibles=grupos_disponibles,
                         filtro_grupo=filtro_grupo,
                         filtro_mes=filtro_mes,
                         filtro_anio=filtro_anio,
                         total_reportes=total_reportes,
                         reportes_este_mes=reportes_este_mes,
                         fecha_hoy=datetime.now().date().isoformat())

@app.route('/admin/descargar-reporte/<int:reporte_id>')
@require_profesor
def descargar_reporte_guardado(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    # AGREGAR VALIDACIÓN: Verificar si el archivo existe
    if not reporte.archivo_url:
        flash('El archivo de este reporte no está disponible', 'danger')
        return redirect(url_for('ver_reportes_asistencia'))
    
    try:
        return descargar_archivo(reporte.archivo_url, reporte.nombre_archivo, 'reportes')
    except Exception as e:
        log_error(f"Error al descargar reporte: {str(e)}")
        flash(f'Error: El archivo no existe o fue eliminado', 'danger')
        return redirect(url_for('ver_reportes_asistencia'))

@app.route('/admin/eliminar-reporte/<int:reporte_id>')
@require_profesor
def eliminar_reporte(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        if reporte.archivo_url and reporte.archivo_url.startswith('http') and s3_manager.is_configured:
            try:
                key = f"reportes/{reporte.nombre_archivo}"
                s3_manager.delete_file(key)
            except S3UploadError as e:
                log_warning(f"No se pudo eliminar de S3: {e}")
        
        db.session.delete(reporte)
        db.session.commit()
        
        flash('Reporte eliminado correctamente', 'success')
        
    except Exception as e:
        log_error(f"Error al eliminar reporte: {str(e)}")
        flash(f'Error al eliminar reporte: {str(e)}', 'danger')
    
    return redirect(url_for('ver_reportes_asistencia'))

# --- RUTAS DE ALUMNOS ---

@app.route('/alumnos')
@require_alumno
def panel_alumnos():
    alumno = UsuarioAlumno.query.get(session['alumno_id'])
    
    mis_entregas = EntregaAlumno.query.filter_by(alumno_id=alumno.id).order_by(EntregaAlumno.fecha_entrega.desc()).all()
    mi_grupo_exacto = session['alumno_grado']
    mis_cuestionarios = Cuestionario.query.filter_by(grado=mi_grupo_exacto).order_by(Cuestionario.fecha.desc()).all()
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(3).all()
    
    total_estrellas = sum(e.estrellas for e in mis_entregas if e.estrellas > 0)
    entregas_calificadas = sum(1 for e in mis_entregas if e.estrellas > 0)
    promedio = total_estrellas / entregas_calificadas if entregas_calificadas > 0 else 0

    return render_template('alumnos/panel_alumnos.html', 
                         alumno=alumno,
                         entregas=mis_entregas,
                         cuestionarios=mis_cuestionarios,
                         anuncios=anuncios,
                         promedio=round(promedio, 1),
                         entregas_calificadas=entregas_calificadas)

@app.route('/alumnos/subir', methods=['POST'])
@require_alumno
def subir_tarea():
    if 'archivo' not in request.files:
        flash('No se subió archivo', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    archivo = request.files['archivo']
    titulo_tarea = request.form.get('titulo_tarea', '').strip()
    
    if archivo.filename == '':
        flash('Ningún archivo seleccionado', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    if not titulo_tarea:
        flash('⚠️ Debes escribir el nombre de la tarea', 'warning')
        return redirect(url_for('panel_alumnos'))

    alumno = UsuarioAlumno.query.get(session['alumno_id'])
    
    try:
        ruta, es_s3 = guardar_archivo(archivo)
        
        nueva_entrega = EntregaAlumno(
            alumno_id=alumno.id,
            nombre_alumno=alumno.nombre_completo,
            grado_grupo=alumno.grado_grupo,
            titulo_tarea=titulo_tarea,
            archivo_url=ruta
        )
        
        db.session.add(nueva_entrega)
        db.session.commit()
        
        flash('¡Tarea enviada con éxito! El profesor la revisará pronto.', 'success')
    except (FileValidationError, AppError, DiskSpaceError) as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('panel_alumnos'))

# --- RUTAS DE INVENTARIO ---

@app.route('/admin/inventario')
@require_profesor
def inventario():
    equipos = Equipo.query.order_by(Equipo.id.desc()).all()
    return render_template('admin/inventario.html', equipos=equipos)

@app.route('/admin/inventario/agregar', methods=['POST'])
@require_profesor
def agregar_equipo():
    nuevo_equipo = Equipo(
        tipo=request.form['tipo'],
        marca=request.form['marca'],
        modelo=request.form['modelo'],
        estado=request.form['estado'],
        qr_data=f"ME-{int(datetime.now().timestamp())}"
    )
    
    db.session.add(nuevo_equipo)
    db.session.commit()
    
    flash('Equipo agregado correctamente', 'success')
    return redirect(url_for('inventario'))

@app.route('/admin/inventario/eliminar/<int:id>')
@require_profesor
def eliminar_equipo(id):
    equipo = Equipo.query.get_or_404(id)
    
    db.session.delete(equipo)
    db.session.commit()
    
    flash('Equipo eliminado del inventario', 'warning')
    return redirect(url_for('inventario'))

@app.route('/admin/generar_qr_img/<int:id>')
@require_profesor
def generar_qr_img(id):
    equipo = Equipo.query.get_or_404(id)
    info_qr = f"PROPIEDAD ESCUELA MARIANO ESCOBEDO\nID: {equipo.id}\nTipo: {equipo.tipo}\nMarca: {equipo.marca}\nModelo: {equipo.modelo}"

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(info_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)

    return send_file(img_io, mimetype='image/png')

# --- RUTAS DE MANTENIMIENTO ---

@app.route('/admin/mantenimiento')
@require_profesor
def mantenimiento():
    pendientes = Mantenimiento.query.filter_by(fecha_reparacion=None).all()
    historial = Mantenimiento.query.filter(Mantenimiento.fecha_reparacion != None).order_by(Mantenimiento.fecha_reparacion.desc()).limit(10).all()
    equipos = Equipo.query.all()
    
    return render_template('admin/mantenimiento.html', pendientes=pendientes, historial=historial, equipos=equipos)

@app.route('/admin/mantenimiento/reportar', methods=['POST'])
@require_profesor
def reportar_falla():
    equipo_id = request.form['equipo_id']
    descripcion = request.form['descripcion']
    
    nuevo_reporte = Mantenimiento(equipo_id=equipo_id, descripcion_falla=descripcion)
    
    equipo = Equipo.query.get(equipo_id)
    equipo.estado = "En Reparación"
    
    db.session.add(nuevo_reporte)
    db.session.commit()
    
    flash('Falla reportada. El equipo pasó a estado de reparación.', 'warning')
    return redirect(url_for('mantenimiento'))

@app.route('/admin/mantenimiento/solucionar', methods=['POST'])
@require_profesor
def solucionar_falla():
    reporte_id = request.form['reporte_id']
    solucion = request.form['solucion']
    
    reporte = Mantenimiento.query.get(reporte_id)
    reporte.fecha_reparacion = datetime.utcnow()
    reporte.solucion = solucion
    reporte.equipo.estado = "Funcional"
    
    db.session.commit()
    flash('¡Equipo reparado exitosamente!', 'success')
    return redirect(url_for('mantenimiento'))

# --- RUTAS DE ANUNCIOS ---

@app.route('/admin/anuncios')
@require_profesor
def gestionar_anuncios():
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).all()
    return render_template('admin/anuncios.html', anuncios=anuncios)

@app.route('/admin/anuncios/publicar', methods=['POST'])
@require_profesor
def publicar_anuncio():
    titulo = request.form['titulo']
    contenido = request.form['contenido']
    
    nuevo_anuncio = Anuncio(titulo=titulo, contenido=contenido)
    
    db.session.add(nuevo_anuncio)
    db.session.commit()
    
    flash('¡Anuncio publicado en la página principal!', 'success')
    return redirect(url_for('gestionar_anuncios'))

@app.route('/admin/anuncios/eliminar/<int:id>')
@require_profesor
def eliminar_anuncio(id):
    anuncio = Anuncio.query.get_or_404(id)
    
    db.session.delete(anuncio)
    db.session.commit()
    
    flash('Anuncio eliminado.', 'secondary')
    return redirect(url_for('gestionar_anuncios'))

# --- RUTAS DE CUESTIONARIOS ---

@app.route('/admin/cuestionarios')
@require_profesor
def gestionar_cuestionarios():
    cuestionarios = Cuestionario.query.order_by(Cuestionario.fecha.desc()).all()
    return render_template('admin/cuestionarios.html', cuestionarios=cuestionarios)

@app.route('/admin/cuestionarios/publicar', methods=['POST'])
@require_profesor
def publicar_cuestionario():
    grado = request.form['grado']
    grupo = request.form['grupo']
    target = f"{grado}{grupo}"
    
    nuevo = Cuestionario(
        titulo=request.form['titulo'],
        url=request.form['url'],
        grado=target
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    flash(f'Cuestionario asignado exclusivamente al grupo {target}.', 'success')
    return redirect(url_for('gestionar_cuestionarios'))

@app.route('/admin/cuestionarios/eliminar/<int:id>')
@require_profesor
def eliminar_cuestionario(id):
    item = Cuestionario.query.get_or_404(id)
    
    db.session.delete(item)
    db.session.commit()
    
    flash('Cuestionario eliminado.', 'secondary')
    return redirect(url_for('gestionar_cuestionarios'))

# --- GESTIÓN DEL BANCO DE CUESTIONARIOS ---

@app.route('/admin/banco')
@require_profesor
def gestionar_banco():
    banco = BancoCuestionario.query.order_by(BancoCuestionario.fecha_creacion.desc()).all()
    return render_template('admin/Banco_cuestionarios.html', banco=banco)

@app.route('/admin/banco/agregar', methods=['POST'])
@require_profesor
def agregar_al_banco():
    nuevo = BancoCuestionario(
        titulo=request.form['titulo'],
        url=request.form['url']
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    flash('Cuestionario guardado en la bodega.', 'success')
    return redirect(url_for('gestionar_banco'))

@app.route('/admin/banco/eliminar/<int:id>')
@require_profesor
def eliminar_del_banco(id):
    item = BancoCuestionario.query.get_or_404(id)
    
    db.session.delete(item)
    db.session.commit()
    
    flash('Plantilla eliminada.', 'warning')
    return redirect(url_for('gestionar_banco'))

@app.route('/admin/banco/asignar', methods=['POST'])
@require_profesor
def asignar_desde_banco():
    plantilla_id = request.form['plantilla_id']
    grado = request.form['grado']
    grupo = request.form['grupo']
    target = f"{grado}{grupo}"
    
    original = BancoCuestionario.query.get(plantilla_id)
    
    if original:
        nuevo_activo = Cuestionario(
            titulo=original.titulo,
            url=original.url,
            grado=target
        )
        
        db.session.add(nuevo_activo)
        db.session.commit()
        
        flash(f'¡Examen "{original.titulo}" liberado para el grupo {target}!', 'success')
    else:
        flash('Error al buscar la plantilla.', 'danger')
        
    return redirect(url_for('gestionar_banco'))

# --- RUTAS PÚBLICAS DE GRADOS ---

@app.route('/grado/<int:numero_grado>')
def ver_grado(numero_grado):
    actividad = ActividadGrado.query.filter_by(grado=numero_grado).first()
    return render_template('publico/ver_grado.html', grado=numero_grado, actividad=actividad)

@app.route('/admin/grados', methods=['GET', 'POST'])
@require_profesor
def gestionar_grados():
    if request.method == 'POST':
        grado_id = int(request.form['grado'])
        titulo = request.form['titulo']
        descripcion = request.form['descripcion']
        
        actividad = ActividadGrado.query.filter_by(grado=grado_id).first()
        
        if not actividad:
            actividad = ActividadGrado(grado=grado_id)
        
        actividad.titulo = titulo
        actividad.descripcion = descripcion
        actividad.fecha_actualizacion = datetime.utcnow()
        
        if actividad not in db.session:
            db.session.add(actividad)
        
        db.session.commit()
        flash(f'¡Información de {grado_id}° actualizada!', 'success')
        return redirect(url_for('gestionar_grados'))

    actividades = ActividadGrado.query.all()
    info_grados = {a.grado: a for a in actividades}
    
    return render_template('admin/gestionar_grados.html', info_grados=info_grados)

# --- GESTIÓN DE HORARIOS ---

@app.route('/admin/horarios')
@require_profesor
def gestionar_horarios():
    horarios = Horario.query.all()
    return render_template('admin/horarios.html', horarios=horarios)

@app.route('/admin/horarios/agregar', methods=['POST'])
@require_profesor
def agregar_horario():
    nuevo = Horario(
        dia=request.form['dia'],
        grados=request.form['grados'],
        hora=request.form['hora']
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    flash('Horario agregado correctamente.', 'success')
    return redirect(url_for('gestionar_horarios'))

@app.route('/admin/horarios/eliminar/<int:id>')
@require_profesor
def eliminar_horario(id):
    horario = Horario.query.get_or_404(id)
    
    db.session.delete(horario)
    db.session.commit()
    
    flash('Horario eliminado.', 'warning')
    return redirect(url_for('gestionar_horarios'))

# --- GESTIÓN DE PLATAFORMAS ---

@app.route('/admin/plataformas')
@require_profesor
def gestionar_plataformas():
    plataformas = Plataforma.query.all()
    return render_template('admin/plataformas.html', plataformas=plataformas)

@app.route('/admin/plataformas/agregar', methods=['POST'])
@require_profesor
def agregar_plataforma():
    nueva = Plataforma(
        nombre=request.form['nombre'],
        url=request.form['url'],
        icono=request.form['icono']
    )
    
    db.session.add(nueva)
    db.session.commit()
    
    flash('Plataforma agregada.', 'success')
    return redirect(url_for('gestionar_plataformas'))

@app.route('/admin/plataformas/eliminar/<int:id>')
@require_profesor
def eliminar_plataforma(id):
    p = Plataforma.query.get_or_404(id)
    
    db.session.delete(p)
    db.session.commit()
    
    flash('Plataforma eliminada.', 'warning')
    return redirect(url_for('gestionar_plataformas'))

# --- GESTIÓN DE ENTREGAS ---

@app.route('/admin/entregas')
@require_profesor
def gestionar_entregas():
    filtro = request.args.get('grado')
    
    query = EntregaAlumno.query.join(UsuarioAlumno)
    
    if filtro and filtro != 'Todos':
        query = query.filter(UsuarioAlumno.grado_grupo == filtro)
    
    entregas = query.order_by(EntregaAlumno.fecha_entrega.desc()).all()
    
    return render_template('admin/entregas_alumnos.html', 
                         entregas=entregas, 
                         filtro_actual=filtro)

# --- GESTIÓN DE RECURSOS ---

@app.route('/admin/recursos')
@require_profesor
def gestionar_recursos():
    recursos = Recurso.query.order_by(Recurso.fecha.desc()).all()
    return render_template('admin/recursos.html', recursos=recursos)

@app.route('/admin/recursos/subir', methods=['POST'])
@require_profesor
def subir_recurso():
    archivo = request.files.get('archivo')
    titulo = request.form.get('titulo')

    if archivo and titulo:
        try:
            ruta_archivo, es_s3 = guardar_archivo(archivo)
            
            ext = archivo.filename.split('.')[-1].lower()
            if ext == 'pdf':
                tipo = 'PDF'
            elif ext in ['doc', 'docx']:
                tipo = 'WORD'
            else:
                tipo = 'OTRO'

            nuevo = Recurso(
                titulo=titulo, 
                archivo_url=ruta_archivo,
                tipo_archivo=tipo
            )
            
            db.session.add(nuevo)
            db.session.commit()
            
            flash('Recurso publicado correctamente.', 'success')
        except (FileValidationError, AppError, DiskSpaceError) as e:
            flash(f'Error: {str(e)}', 'danger')
            
    return redirect(url_for('gestionar_recursos'))

@app.route('/admin/recursos/eliminar/<int:id>')
@require_profesor
def eliminar_recurso(id):
    recurso = Recurso.query.get_or_404(id)
    
    db.session.delete(recurso)
    db.session.commit()
    
    flash('Recurso eliminado de la lista.', 'warning')
    return redirect(url_for('gestionar_recursos'))

# --- RUTAS PARA SERVIR ARCHIVOS ---

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/ver-archivo/<path:archivo_path>')
def ver_archivo(archivo_path):
    try:
        if archivo_path.startswith('uploads/'):
            if s3_manager.is_configured:
                file_stream, content_type = s3_manager.download_file(archivo_path)
                
                if file_stream:
                    filename = archivo_path.split('/')[-1]
                    
                    return send_file(
                        file_stream,
                        mimetype=content_type,
                        as_attachment=False,
                        download_name=filename
                    )
            else:
                flash('Configuración de almacenamiento no disponible', 'danger')
                return redirect(url_for('index'))
        
        else:
            filename = archivo_path
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            
            if os.path.exists(file_path):
                if filename.endswith('.pdf'):
                    mimetype = 'application/pdf'
                elif filename.endswith(('.doc', '.docx')):
                    mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                else:
                    mimetype = 'application/octet-stream'
                
                return send_file(
                    file_path,
                    mimetype=mimetype,
                    as_attachment=False,
                    download_name=filename
                )
            else:
                flash('Archivo no encontrado', 'danger')
                return redirect(url_for('index'))
                
    except Exception as e:
        log_error(f"Error al servir archivo: {str(e)}")
        flash(f'Error al cargar el archivo: {str(e)}', 'danger')
        return redirect(url_for('index'))

# --- SISTEMA DE BOLETAS Y REPORTES ---

@app.route('/admin/boletas/config', methods=['GET', 'POST'])
@require_profesor
def configurar_boletas():
    if request.method == 'POST':
        grado = request.form['grado']
        criterio = request.form['criterio']
        nuevo = CriterioBoleta(grado=grado, nombre=criterio)
        
        db.session.add(nuevo)
        db.session.commit()
        
        flash('Criterio agregado correctamente.', 'success')
    
    criterios = CriterioBoleta.query.order_by(CriterioBoleta.grado).all()
    return render_template('admin/boletas_config.html', criterios=criterios)

@app.route('/admin/boletas/borrar-criterio/<int:id>')
@require_profesor
def borrar_criterio(id):
    c = CriterioBoleta.query.get_or_404(id)
    
    db.session.delete(c)
    db.session.commit()
    
    return redirect(url_for('configurar_boletas'))

@app.route('/admin/boletas/generar', methods=['GET', 'POST'])
@require_profesor
def generar_boleta():
    alumno = None
    criterios = []
    
    filtro_grado = request.args.get('filtro_grado')
    query = UsuarioAlumno.query
    
    if filtro_grado and filtro_grado != 'Todos':
        query = query.filter_by(grado_grupo=filtro_grado)
    
    alumnos = query.order_by(UsuarioAlumno.grado_grupo, UsuarioAlumno.nombre_completo).all()

    alumno_id = request.args.get('alumno_id')
    if alumno_id:
        alumno = UsuarioAlumno.query.get_or_404(alumno_id)
        grado_num = ''.join(filter(str.isdigit, alumno.grado_grupo))
        criterios = CriterioBoleta.query.filter_by(grado=grado_num).all()

    if request.method == 'POST':
        datos_evaluacion = {}
        promedio = 0
        total_puntos = 0
        conteo = 0
        periodo = request.form.get('periodo', 'Sin especificar')
        
        for key, value in request.form.items():
            if key.startswith('nota_'):
                criterio_nombre = key.replace('nota_', '')
                nota = float(value) if value else 0
                datos_evaluacion[criterio_nombre] = nota
                total_puntos += nota
                conteo += 1
        
        if conteo > 0:
            promedio = round(total_puntos / conteo, 1)
        
        try:
            file_url, buffer_pdf, nombre_archivo = generar_pdf_boleta(
                alumno, 
                datos_evaluacion, 
                request.form.get('observaciones'),
                promedio,
                periodo
            )
            
            nueva_boleta = BoletaGenerada(
                alumno_id=alumno.id,
                archivo_url=file_url,
                nombre_archivo=nombre_archivo,
                periodo=periodo,
                promedio=promedio,
                observaciones=request.form.get('observaciones'),
                generado_por=session.get('user', 'Sistema')
            )
            
            db.session.add(nueva_boleta)
            db.session.commit()
            
            flash('✅ Boleta generada y guardada correctamente', 'success')
            
            return send_file(
                buffer_pdf,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=nombre_archivo
            )
            
        except AppError as e:
            log_error(f"Error al generar boleta: {str(e)}")
            flash(f'Error al generar boleta: {str(e)}', 'danger')
            return redirect(url_for('generar_boleta'))

    return render_template('admin/boleta_form.html', 
                         alumnos=alumnos, 
                         alumno_seleccionado=alumno, 
                         criterios=criterios,
                         filtro_actual=filtro_grado)

@app.route('/admin/boletas/historial')
@require_profesor
def ver_boletas_historial():
    filtro_grado = request.args.get('grado', 'Todos')
    filtro_periodo = request.args.get('periodo', '')
    
    query = BoletaGenerada.query.join(UsuarioAlumno)
    
    if filtro_grado and filtro_grado != 'Todos':
        query = query.filter(UsuarioAlumno.grado_grupo == filtro_grado)
    
    if filtro_periodo:
        query = query.filter(BoletaGenerada.periodo.contains(filtro_periodo))
    
    boletas = query.order_by(BoletaGenerada.fecha_generacion.desc()).all()
    
    grupos_disponibles = db.session.query(UsuarioAlumno.grado_grupo).distinct().all()
    grupos_disponibles = sorted([g[0] for g in grupos_disponibles])
    
    total_boletas = BoletaGenerada.query.count()
    boletas_este_mes = BoletaGenerada.query.filter(
        BoletaGenerada.fecha_generacion >= datetime.now().date().replace(day=1)
    ).count()
    
    return render_template('admin/boletas_historial.html',
                         boletas=boletas,
                         grupos_disponibles=grupos_disponibles,
                         filtro_grado=filtro_grado,
                         filtro_periodo=filtro_periodo,
                         total_boletas=total_boletas,
                         boletas_este_mes=boletas_este_mes)

@app.route('/admin/boletas/descargar/<int:boleta_id>')
@require_profesor
def descargar_boleta_guardada(boleta_id):
    boleta = BoletaGenerada.query.get_or_404(boleta_id)
    return descargar_archivo(boleta.archivo_url, boleta.nombre_archivo, 'boletas')

@app.route('/admin/boletas/eliminar/<int:boleta_id>')
@require_profesor
def eliminar_boleta_guardada(boleta_id):
    boleta = BoletaGenerada.query.get_or_404(boleta_id)
    
    try:
        if boleta.archivo_url and boleta.archivo_url.startswith('boletas/') and s3_manager.is_configured:
            try:
                s3_manager.delete_file(boleta.archivo_url)
            except S3UploadError as e:
                log_warning(f"No se pudo eliminar de S3: {e}")
        
        db.session.delete(boleta)
        db.session.commit()
        
        flash('Boleta eliminada correctamente', 'success')
        
    except Exception as e:
        log_error(f"Error al eliminar boleta: {str(e)}")
        flash(f'Error al eliminar boleta: {str(e)}', 'danger')
    
    return redirect(url_for('ver_boletas_historial'))

# --- RUTAS DE MENSAJES FLOTANTES ---

@app.route('/admin/mensajes-flotantes')
@require_profesor
def gestionar_mensajes_flotantes():
    mensajes = MensajeFlotante.query.filter_by(activo=True).order_by(MensajeFlotante.fecha_creacion.desc()).all()
    
    mensajes_con_stats = []
    for msg in mensajes:
        total_alumnos = UsuarioAlumno.query.filter_by(grado_grupo=msg.grado_grupo, activo=True).count()
        leidos = MensajeLeido.query.filter_by(mensaje_id=msg.id).count()
        mensajes_con_stats.append({
            'mensaje': msg,
            'total_alumnos': total_alumnos,
            'leidos': leidos
        })
    
    return render_template('admin/mensajes_flotantes.html', mensajes_con_stats=mensajes_con_stats)

@app.route('/admin/mensajes-flotantes/crear', methods=['POST'])
@require_profesor
def crear_mensaje_flotante():
    grado = request.form.get('grado')
    grupo = request.form.get('grupo')
    contenido = request.form.get('contenido')
    
    if not contenido or not grado or not grupo:
        flash('Debes completar todos los campos', 'danger')
        return redirect(url_for('gestionar_mensajes_flotantes'))
    
    grado_grupo = f"{grado}{grupo}"
    
    nuevo_mensaje = MensajeFlotante(
        grado_grupo=grado_grupo,
        contenido=contenido,
        creado_por=session.get('user', 'Sistema')
    )
    
    db.session.add(nuevo_mensaje)
    db.session.commit()
    
    flash(f'¡Mensaje enviado al grupo {grado_grupo}!', 'success')
    return redirect(url_for('gestionar_mensajes_flotantes'))

@app.route('/admin/mensajes-flotantes/desactivar/<int:id>')
@require_profesor
def desactivar_mensaje_flotante(id):
    mensaje = MensajeFlotante.query.get_or_404(id)
    mensaje.activo = False
    db.session.commit()
    
    flash('Mensaje desactivado correctamente', 'success')
    return redirect(url_for('gestionar_mensajes_flotantes'))

@app.route('/api/mensajes-flotantes/obtener')
@require_alumno
def obtener_mensajes_flotantes():
    alumno_id = session.get('alumno_id')
    grado_grupo = session.get('alumno_grado')
    
    mensajes_leidos = db.session.query(MensajeLeido.mensaje_id).filter_by(alumno_id=alumno_id).all()
    ids_leidos = [m[0] for m in mensajes_leidos]
    
    mensajes = MensajeFlotante.query.filter_by(
        grado_grupo=grado_grupo,
        activo=True
    ).filter(
        MensajeFlotante.id.notin_(ids_leidos) if ids_leidos else True
    ).order_by(MensajeFlotante.fecha_creacion.desc()).all()
    
    return jsonify([{
        'id': m.id,
        'contenido': m.contenido,
        'fecha': m.fecha_creacion.strftime('%d/%m/%Y %H:%M')
    } for m in mensajes])

@app.route('/api/mensajes-flotantes/marcar-leido/<int:mensaje_id>', methods=['POST'])
@require_alumno
def marcar_mensaje_leido(mensaje_id):
    alumno_id = session.get('alumno_id')
    
    existe = MensajeLeido.query.filter_by(mensaje_id=mensaje_id, alumno_id=alumno_id).first()
    
    if not existe:
        nuevo_leido = MensajeLeido(mensaje_id=mensaje_id, alumno_id=alumno_id)
        
        db.session.add(nuevo_leido)
        db.session.commit()
    
    return jsonify({'status': 'ok'})

# --- RUTAS PARA GESTIÓN DE PAGOS ---

@app.route('/admin/pagos')
@require_profesor
def gestionar_pagos():
    filtro_grado = request.args.get('grado', 'Todos')
    filtro_estado = request.args.get('estado', 'todos')
    
    query = Pago.query.join(UsuarioAlumno)
    
    if filtro_grado != 'Todos':
        query = query.filter(UsuarioAlumno.grado_grupo == filtro_grado)
    
    if filtro_estado != 'todos':
        query = query.filter(Pago.estado == filtro_estado)
    
    pagos = query.order_by(Pago.fecha_creacion.desc()).all()
    
    grupos_disponibles = db.session.query(UsuarioAlumno.grado_grupo).distinct().all()
    grupos_disponibles = sorted([g[0] for g in grupos_disponibles])
    
    total_pagos = Pago.query.count()
    monto_total_cobrado = db.session.query(db.func.sum(ReciboPago.monto)).scalar() or 0
    pagos_pendientes = Pago.query.filter_by(estado='pendiente').count()
    
    return render_template('admin/pagos.html',
                         pagos=pagos,
                         grupos_disponibles=grupos_disponibles,
                         filtro_grado=filtro_grado,
                         filtro_estado=filtro_estado,
                         total_pagos=total_pagos,
                         monto_total_cobrado=monto_total_cobrado,
                         pagos_pendientes=pagos_pendientes)

@app.route('/admin/pagos/crear', methods=['GET', 'POST'])
@require_profesor
def crear_pago():
    if request.method == 'POST':
        tipo_creacion = request.form.get('tipo_creacion')
        concepto = request.form.get('concepto')
        monto = float(request.form.get('monto'))
        tipo_pago = request.form.get('tipo_pago')
        fecha_vencimiento = request.form.get('fecha_vencimiento')
        
        try:
            if tipo_creacion == 'individual':
                alumno_id = int(request.form.get('alumno_id'))
                alumnos = [UsuarioAlumno.query.get(alumno_id)]
            else:
                grado = request.form.get('grado')
                grupo = request.form.get('grupo')
                grado_grupo = f"{grado}{grupo}"
                alumnos = UsuarioAlumno.query.filter_by(
                    grado_grupo=grado_grupo,
                    activo=True
                ).all()
            
            pagos_creados = 0
            for alumno in alumnos:
                nuevo_pago = Pago(
                    alumno_id=alumno.id,
                    concepto=concepto,
                    monto_total=monto,
                    monto_pagado=0,
                    monto_pendiente=monto,
                    tipo_pago=tipo_pago,
                    estado='pendiente',
                    grado_grupo=alumno.grado_grupo,
                    creado_por=session.get('user', 'Sistema'),
                    fecha_vencimiento=datetime.strptime(fecha_vencimiento, '%Y-%m-%d').date() if fecha_vencimiento else None
                )
                db.session.add(nuevo_pago)
                pagos_creados += 1
            
            db.session.commit()
            flash(f'✅ {pagos_creados} pago(s) creado(s) correctamente', 'success')
            return redirect(url_for('gestionar_pagos'))
            
        except Exception as e:
            db.session.rollback()
            log_error(f"Error al crear pagos: {str(e)}")
            flash(f'Error al crear pagos: {str(e)}', 'danger')
    
    alumnos = UsuarioAlumno.query.filter_by(activo=True).order_by(UsuarioAlumno.nombre_completo).all()
    grupos_disponibles = db.session.query(UsuarioAlumno.grado_grupo).distinct().all()
    grupos_disponibles = sorted([g[0] for g in grupos_disponibles])
    
    return render_template('admin/crear_pago.html',
                         alumnos=alumnos,
                         grupos_disponibles=grupos_disponibles)

@app.route('/admin/pagos/<int:pago_id>/registrar-pago', methods=['POST'])
@require_profesor
def registrar_pago(pago_id):
    pago = Pago.query.get_or_404(pago_id)
    
    try:
        monto_pagado = float(request.form.get('monto_pagado'))
        metodo_pago = request.form.get('metodo_pago')
        observaciones = request.form.get('observaciones', '')
        
        if monto_pagado <= 0:
            flash('El monto debe ser mayor a 0', 'danger')
            return redirect(url_for('gestionar_pagos'))
        
        if monto_pagado > pago.monto_pendiente:
            flash(f'El monto no puede ser mayor al pendiente (${pago.monto_pendiente:,.2f})', 'danger')
            return redirect(url_for('gestionar_pagos'))
        
        fecha_actual = datetime.now()
        numero_recibo = f"REC-{fecha_actual.strftime('%Y%m%d%H%M%S')}-{pago.id}"
        
        nuevo_recibo = ReciboPago(
            pago_id=pago.id,
            numero_recibo=numero_recibo,
            monto=monto_pagado,
            metodo_pago=metodo_pago,
            recibido_por=session.get('user', 'Sistema'),
            observaciones=observaciones
        )
        
        db.session.add(nuevo_recibo)
        
        alumno = UsuarioAlumno.query.get(pago.alumno_id)
        buffer_pdf = generar_recibo_pdf(nuevo_recibo, alumno, pago)
        
        nombre_archivo = f"recibo_{numero_recibo}.pdf"
        key_s3 = f"pagos/recibos/{pago.grado_grupo}/{nombre_archivo}"
        
        if s3_manager.is_configured:
            try:
                url_archivo = s3_manager.upload_file(buffer_pdf, key_s3, 'application/pdf')
                nuevo_recibo.archivo_url = key_s3
                nuevo_recibo.nombre_archivo = nombre_archivo
            except S3UploadError as e:
                log_warning(f"No se pudo subir a S3: {e}")
                ruta_local = os.path.join(UPLOAD_FOLDER, 'pagos', 'recibos')
                os.makedirs(ruta_local, exist_ok=True)
                with open(os.path.join(ruta_local, nombre_archivo), 'wb') as f:
                    f.write(buffer_pdf.getvalue())
                nuevo_recibo.archivo_url = f"pagos/recibos/{nombre_archivo}"
                nuevo_recibo.nombre_archivo = nombre_archivo
        else:
            ruta_local = os.path.join(UPLOAD_FOLDER, 'pagos', 'recibos')
            os.makedirs(ruta_local, exist_ok=True)
            with open(os.path.join(ruta_local, nombre_archivo), 'wb') as f:
                f.write(buffer_pdf.getvalue())
            nuevo_recibo.archivo_url = f"pagos/recibos/{nombre_archivo}"
            nuevo_recibo.nombre_archivo = nombre_archivo
        
        pago.monto_pagado += monto_pagado
        pago.monto_pendiente -= monto_pagado
        
        if pago.monto_pendiente <= 0:
            pago.estado = 'completado'
        else:
            pago.estado = 'parcial'
        
        db.session.commit()
        
        flash(f'✅ Pago registrado correctamente. Recibo: {numero_recibo}', 'success')
        
        buffer_pdf.seek(0)
        return send_file(
            buffer_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=nombre_archivo
        )
        
    except Exception as e:
        db.session.rollback()
        log_error(f"Error al registrar pago: {str(e)}")
        flash(f'Error al registrar pago: {str(e)}', 'danger')
        return redirect(url_for('gestionar_pagos'))

@app.route('/admin/pagos/recibos')
@require_profesor
def ver_recibos():
    filtro_grado = request.args.get('grado', 'Todos')
    
    query = ReciboPago.query.join(Pago).join(UsuarioAlumno)
    
    if filtro_grado != 'Todos':
        query = query.filter(UsuarioAlumno.grado_grupo == filtro_grado)
    
    recibos = query.order_by(ReciboPago.fecha_pago.desc()).all()
    
    grupos_disponibles = db.session.query(UsuarioAlumno.grado_grupo).distinct().all()
    grupos_disponibles = sorted([g[0] for g in grupos_disponibles])
    
    return render_template('admin/recibos.html',
                         recibos=recibos,
                         grupos_disponibles=grupos_disponibles,
                         filtro_grado=filtro_grado)

@app.route('/admin/pagos/recibos/descargar/<int:recibo_id>')
@require_profesor
def descargar_recibo(recibo_id):
    recibo = ReciboPago.query.get_or_404(recibo_id)
    
    try:
        if recibo.archivo_url and s3_manager.is_configured:
            file_stream, content_type = s3_manager.download_file(recibo.archivo_url)
            return send_file(
                file_stream,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=recibo.nombre_archivo
            )
        else:
            return send_from_directory(
                os.path.join(UPLOAD_FOLDER, 'pagos', 'recibos'),
                recibo.nombre_archivo,
                as_attachment=True
            )
    except Exception as e:
        log_error(f"Error al descargar recibo: {str(e)}")
        flash(f'Error al descargar recibo: {str(e)}', 'danger')
        return redirect(url_for('ver_recibos'))

@app.route('/admin/pagos/<int:pago_id>/eliminar')
@require_profesor
def eliminar_pago(pago_id):
    pago = Pago.query.get_or_404(pago_id)
    
    try:
        # Primero eliminar archivos de S3
        recibos_a_eliminar = list(pago.recibos)
        
        for recibo in recibos_a_eliminar:
            if recibo.archivo_url and s3_manager.is_configured:
                try:
                    s3_manager.delete_file(recibo.archivo_url)
                    log_info(f"Archivo eliminado: {recibo.archivo_url}")
                except S3UploadError as e:
                    log_warning(f"No se pudo eliminar recibo de S3: {e}")
            
            db.session.delete(recibo)
        
        db.session.flush()
        
        db.session.delete(pago)
        db.session.commit()
        
        flash('Pago y recibos eliminados correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        log_error(f"Error al eliminar pago: {str(e)}")
        flash(f'Error al eliminar pago: {str(e)}', 'danger')
    
    return redirect(url_for('gestionar_pagos'))

# --- NUEVAS RUTAS PARA SISTEMA DE ARCHIVOS ---

@app.route('/alumno/solicitar-archivo', methods=['GET', 'POST'])
@require_alumno
def solicitar_archivo():
    """Permite al alumno solicitar un archivo al profesor"""
    alumno_id = session.get('alumno_id')
    
    if request.method == 'POST':
        tipo_documento = request.form.get('tipo_documento')
        mensaje = request.form.get('mensaje')
        
        if not tipo_documento or not mensaje:
            flash('Debe completar todos los campos', 'danger')
            return redirect(url_for('solicitar_archivo'))
        
        nueva_solicitud = SolicitudArchivo(
            alumno_id=alumno_id,
            tipo_documento=tipo_documento,
            mensaje=mensaje,
            estado='pendiente'
        )
        
        db.session.add(nueva_solicitud)
        db.session.commit()
        
        flash('✅ Solicitud enviada correctamente. El profesor la revisará pronto.', 'success')
        return redirect(url_for('panel_alumnos'))
    
    alumno = UsuarioAlumno.query.get(alumno_id)
    solicitudes = SolicitudArchivo.query.filter_by(alumno_id=alumno_id).order_by(SolicitudArchivo.fecha_solicitud.desc()).all()
    
    pendientes = SolicitudArchivo.query.filter_by(alumno_id=alumno_id, estado='pendiente').count()
    
    return render_template('alumnos/solicitar_archivo.html', 
                         alumno=alumno,
                         solicitudes=solicitudes,
                         pendientes=pendientes)

@app.route('/alumno/mis-archivos')
@require_alumno
def ver_mis_archivos():
    """Ver todos los archivos que el profesor ha enviado al alumno"""
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    archivos = ArchivoEnviado.query.filter_by(alumno_id=alumno_id).order_by(ArchivoEnviado.fecha_envio.desc()).all()
    
    no_leidos = ArchivoEnviado.query.filter_by(alumno_id=alumno_id, leido=False).count()
    
    return render_template('alumnos/mis_archivos.html',
                         alumno=alumno,
                         archivos=archivos,
                         no_leidos=no_leidos)

@app.route('/alumno/archivo/<int:archivo_id>/descargar')
@require_alumno
def descargar_archivo_alumno(archivo_id):
    """Descargar un archivo enviado por el profesor"""
    alumno_id = session.get('alumno_id')
    archivo = ArchivoEnviado.query.get_or_404(archivo_id)
    
    if archivo.alumno_id != alumno_id:
        flash('No tienes permiso para descargar este archivo', 'danger')
        return redirect(url_for('ver_mis_archivos'))
    
    if not archivo.leido:
        archivo.leido = True
        archivo.fecha_lectura = datetime.now()
        db.session.commit()
    
    try:
        if archivo.archivo_url and s3_manager.is_configured:
            file_stream, content_type = s3_manager.download_file(archivo.archivo_url)
            return send_file(
                file_stream,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=archivo.nombre_archivo
            )
        else:
            return send_from_directory(
                os.path.join(UPLOAD_FOLDER, 'archivos_enviados'),
                archivo.nombre_archivo,
                as_attachment=True
            )
    except Exception as e:
        log_error(f"Error al descargar archivo: {str(e)}")
        flash(f'Error al descargar archivo: {str(e)}', 'danger')
        return redirect(url_for('ver_mis_archivos'))

@app.route('/admin/solicitudes-archivo')
@require_profesor
def ver_solicitudes_archivo():
    """Ver todas las solicitudes de archivos de los alumnos"""
    filtro_estado = request.args.get('estado', 'todas')
    
    query = SolicitudArchivo.query.join(UsuarioAlumno)
    
    if filtro_estado != 'todas':
        query = query.filter(SolicitudArchivo.estado == filtro_estado)
    
    solicitudes = query.order_by(SolicitudArchivo.fecha_solicitud.desc()).all()
    
    pendientes = SolicitudArchivo.query.filter_by(estado='pendiente').count()
    
    return render_template('admin/solicitudes_archivo.html',
                         solicitudes=solicitudes,
                         filtro_estado=filtro_estado,
                         pendientes=pendientes)

@app.route('/admin/solicitudes-archivo/<int:solicitud_id>/responder', methods=['GET', 'POST'])
@require_profesor
def responder_solicitud_archivo(solicitud_id):
    """Responder a una solicitud enviando un archivo"""
    solicitud = SolicitudArchivo.query.get_or_404(solicitud_id)
    
    if request.method == 'POST':
        archivo = request.files.get('archivo')
        mensaje = request.form.get('mensaje', '')
        
        if not archivo:
            flash('Debe seleccionar un archivo PDF', 'danger')
            return redirect(url_for('responder_solicitud_archivo', solicitud_id=solicitud_id))
        
        if not archivo.filename.lower().endswith('.pdf'):
            flash('Solo se permiten archivos PDF', 'danger')
            return redirect(url_for('responder_solicitud_archivo', solicitud_id=solicitud_id))
        
        try:
            file_stream = BytesIO(archivo.read())
            validator = FileValidator()
            validator.validate(file_stream, archivo.filename)
            
            nombre_archivo = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            nombre_archivo = f"{timestamp}_{nombre_archivo}"
            
            if s3_manager.is_configured:
                key_s3 = f"archivos_enviados/{solicitud.alumno.grado_grupo}/{nombre_archivo}"
                archivo_url = s3_manager.upload_file(file_stream, key_s3, 'application/pdf')
                archivo_url = key_s3
            else:
                ruta_local = os.path.join(UPLOAD_FOLDER, 'archivos_enviados')
                os.makedirs(ruta_local, exist_ok=True)
                archivo_path = os.path.join(ruta_local, nombre_archivo)
                file_stream.seek(0)
                with open(archivo_path, 'wb') as f:
                    f.write(file_stream.read())
                archivo_url = f"archivos_enviados/{nombre_archivo}"
            
            archivo_enviado = ArchivoEnviado(
                alumno_id=solicitud.alumno_id,
                solicitud_id=solicitud.id,
                titulo=solicitud.tipo_documento,
                mensaje=mensaje,
                archivo_url=archivo_url,
                nombre_archivo=nombre_archivo,
                enviado_por=session.get('user', 'Profesor')
            )
            
            db.session.add(archivo_enviado)
            
            solicitud.estado = 'atendida'
            solicitud.fecha_respuesta = datetime.now()
            
            db.session.commit()
            
            flash(f'✅ Archivo enviado correctamente a {solicitud.alumno.nombre_completo}', 'success')
            return redirect(url_for('ver_solicitudes_archivo'))
            
        except Exception as e:
            db.session.rollback()
            log_error(f"Error al enviar archivo: {str(e)}")
            flash(f'Error al enviar archivo: {str(e)}', 'danger')
            return redirect(url_for('responder_solicitud_archivo', solicitud_id=solicitud_id))
    
    return render_template('admin/responder_solicitud.html', solicitud=solicitud)

@app.route('/admin/enviar-archivo-directo', methods=['GET', 'POST'])
@require_profesor
def enviar_archivo_directo():
    """Enviar archivo a un alumno sin que lo haya solicitado"""
    
    if request.method == 'POST':
        alumno_id = request.form.get('alumno_id')
        titulo = request.form.get('titulo')
        mensaje = request.form.get('mensaje', '')
        archivo = request.files.get('archivo')
        
        if not alumno_id or not titulo or not archivo:
            flash('Debe completar todos los campos obligatorios', 'danger')
            return redirect(url_for('enviar_archivo_directo'))
        
        if not archivo.filename.lower().endswith('.pdf'):
            flash('Solo se permiten archivos PDF', 'danger')
            return redirect(url_for('enviar_archivo_directo'))
        
        try:
            alumno = UsuarioAlumno.query.get(alumno_id)
            if not alumno:
                flash('Alumno no encontrado', 'danger')
                return redirect(url_for('enviar_archivo_directo'))
            
            file_stream = BytesIO(archivo.read())
            validator = FileValidator()
            validator.validate(file_stream, archivo.filename)
            
            nombre_archivo = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            nombre_archivo = f"{timestamp}_{nombre_archivo}"
            
            if s3_manager.is_configured:
                key_s3 = f"archivos_enviados/{alumno.grado_grupo}/{nombre_archivo}"
                archivo_url = s3_manager.upload_file(file_stream, key_s3, 'application/pdf')
                archivo_url = key_s3
            else:
                ruta_local = os.path.join(UPLOAD_FOLDER, 'archivos_enviados')
                os.makedirs(ruta_local, exist_ok=True)
                archivo_path = os.path.join(ruta_local, nombre_archivo)
                file_stream.seek(0)
                with open(archivo_path, 'wb') as f:
                    f.write(file_stream.read())
                archivo_url = f"archivos_enviados/{nombre_archivo}"
            
            archivo_enviado = ArchivoEnviado(
                alumno_id=alumno_id,
                solicitud_id=None,
                titulo=titulo,
                mensaje=mensaje,
                archivo_url=archivo_url,
                nombre_archivo=nombre_archivo,
                enviado_por=session.get('user', 'Profesor')
            )
            
            db.session.add(archivo_enviado)
            db.session.commit()
            
            flash(f'✅ Archivo enviado correctamente a {alumno.nombre_completo}', 'success')
            return redirect(url_for('enviar_archivo_directo'))
            
        except Exception as e:
            db.session.rollback()
            log_error(f"Error al enviar archivo: {str(e)}")
            flash(f'Error al enviar archivo: {str(e)}', 'danger')
            return redirect(url_for('enviar_archivo_directo'))
    
    alumnos = UsuarioAlumno.query.filter_by(activo=True).order_by(UsuarioAlumno.nombre_completo).all()
    return render_template('admin/enviar_archivo_directo.html', alumnos=alumnos)

@app.route('/admin/archivos-enviados')
@require_profesor
def ver_archivos_enviados():
    """Ver historial de todos los archivos enviados"""
    filtro_alumno = request.args.get('alumno', 'todos')
    
    query = ArchivoEnviado.query.join(UsuarioAlumno)
    
    if filtro_alumno != 'todos':
        query = query.filter(ArchivoEnviado.alumno_id == filtro_alumno)
    
    archivos = query.order_by(ArchivoEnviado.fecha_envio.desc()).all()
    alumnos = UsuarioAlumno.query.filter_by(activo=True).order_by(UsuarioAlumno.nombre_completo).all()
    
    return render_template('admin/archivos_enviados.html',
                         archivos=archivos,
                         alumnos=alumnos,
                         filtro_alumno=filtro_alumno)

# --- API PARA NOTIFICACIONES ---

@app.route('/api/archivos-nuevos/cantidad')
@require_alumno
def cantidad_archivos_nuevos():
    """API para obtener la cantidad de archivos no leídos del alumno"""
    alumno_id = session.get('alumno_id')
    cantidad = ArchivoEnviado.query.filter_by(alumno_id=alumno_id, leido=False).count()
    return jsonify({'cantidad': cantidad})

@app.route('/api/solicitudes-pendientes/cantidad')
@require_profesor
def cantidad_solicitudes_pendientes():
    """API para obtener la cantidad de solicitudes pendientes"""
    cantidad = SolicitudArchivo.query.filter_by(estado='pendiente').count()
    return jsonify({'cantidad': cantidad})

# --- INICIALIZADOR ---

with app.app_context():
    db.create_all()
    migrar_bd()

if __name__ == '__main__':
    app.run(debug=True, port=5000)