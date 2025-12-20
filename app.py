[file name]: app.py
[file content begin]
import os
import boto3
import qrcode
import io
import magic
import logging
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# --- NUEVOS IMPORTS PARA GENERAR PDF ---
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

# --- NUEVO: RATE LIMITING ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
app.secret_key = 'clave_secreta_desarrollo'  # Cambiar en producción

# --- NUEVO: CONFIGURACIÓN DE RATE LIMITING ---
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"  # Para producción, usa Redis o Memcached
)

# PALABRA MAESTRA PARA RECUPERAR CONTRASEÑA 
TOKEN_MAESTRO = "treceT1gres"

# NUEVO: La sesión expira tras 10 minutos de inactividad
app.permanent_session_lifetime = timedelta(minutes=10)

# NUEVO: Configuración de validación de archivos
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

class S3Manager:
    """Gestor centralizado para operaciones S3"""
    
    def __init__(self):
        self.endpoint = os.environ.get('S3_ENDPOINT')
        self.key = os.environ.get('S3_KEY')
        self.secret = os.environ.get('S3_SECRET')
        self.bucket = os.environ.get('S3_BUCKET_NAME', 'taller-computo')
        self.is_configured = bool(self.endpoint and self.key and self.secret)
        
        logger.info(f"S3 Configurado: {self.is_configured}")
        if self.is_configured:
            logger.info(f"Bucket: {self.bucket}")
    
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
            logger.info(f"Archivo subido a S3: {key}")
            return f"{self.endpoint}/{self.bucket}/{key}"
        except Exception as e:
            logger.error(f"Error S3 upload: {str(e)}")
            raise S3UploadError(f"Error al subir a S3: {str(e)}")
    
    def download_file(self, key):
        """Descargar archivo desde S3"""
        try:
            client = self.get_client()
            s3_object = client.get_object(Bucket=self.bucket, Key=key)
            file_content = s3_object['Body'].read()
            content_type = s3_object.get('ContentType', 'application/octet-stream')
            
            logger.info(f"Archivo descargado de S3: {key}")
            return BytesIO(file_content), content_type
        except Exception as e:
            logger.error(f"Error S3 download: {str(e)}")
            raise S3UploadError(f"Error al descargar de S3: {str(e)}")
    
    def delete_file(self, key):
        """Eliminar archivo de S3"""
        try:
            client = self.get_client()
            client.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Archivo eliminado de S3: {key}")
        except Exception as e:
            logger.error(f"Error S3 delete: {str(e)}")
            raise S3UploadError(f"Error al eliminar de S3: {str(e)}")

class FileValidator:
    """Validador de archivos centralizado"""
    
    def __init__(self):
        self.max_size = MAX_FILE_SIZE
        self.allowed_extensions = set().union(*ALLOWED_EXTENSIONS.values())
        
        # Mapeo de MIME types a extensiones permitidas
        self.mime_to_ext = {
            'image/png': 'png',
            'image/jpeg': ['jpg', 'jpeg'],
            'image/gif': 'gif',
            'image/webp': 'webp',
            'image/bmp': 'bmp',
            'application/pdf': 'pdf',
            'application/msword': 'doc',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
            'application/vnd.ms-excel': 'xls',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
            'application/vnd.ms-powerpoint': 'ppt',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
            'text/plain': 'txt',
            'application/zip': 'zip',
            'application/x-rar-compressed': 'rar',
            'application/x-7z-compressed': '7z',
        }
    
    def validate(self, file_stream, filename):
        """Validación exhaustiva de archivos"""
        # 1. Verificar nombre del archivo
        if '..' in filename or '/' in filename or '\\' in filename:
            raise FileValidationError("Nombre de archivo inválido")
        
        # 2. Verificar extensión
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if not ext:
            raise FileValidationError("Archivo sin extensión")
        
        if ext not in self.allowed_extensions:
            raise FileValidationError(f"Extensión .{ext} no permitida")
        
        # 3. Verificar tamaño
        file_stream.seek(0, 2)
        size = file_stream.tell()
        file_stream.seek(0)
        
        if size > self.max_size:
            raise FileValidationError(f"Archivo demasiado grande (máx {self.max_size/1024/1024}MB)")
        
        # 4. Verificar tipo real (magic number)
        try:
            file_content = file_stream.read(2048)
            file_stream.seek(0)
            
            mime = magic.from_buffer(file_content, mime=True)
            
            if mime not in self.mime_to_ext:
                raise FileValidationError(f"Tipo de archivo {mime} no permitido")
            
            # Verificar que extensión coincida con MIME real
            expected_exts = self.mime_to_ext[mime]
            if isinstance(expected_exts, list):
                if ext not in expected_exts:
                    raise FileValidationError(f"Extensión .{ext} no coincide con tipo real {mime}")
            elif ext != expected_exts:
                if mime == 'image/jpeg' and ext in ['jpg', 'jpeg']:
                    pass  # jpg y jpeg son ambos JPEG
                else:
                    raise FileValidationError(f"Extensión .{ext} no coincide con tipo real {mime}")
                    
        except Exception as e:
            logger.warning(f"Magic number validation skipped: {str(e)}")
            # Si magic falla, continuamos con las validaciones básicas
        
        # 5. Validar contenido para archivos de texto
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

# --- DECORADORES DE SEGURIDAD MEJORADOS ---

def require_profesor(f):
    """Decorador para rutas exclusivas de profesores"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('tipo_usuario') != 'profesor' or 'user' not in session:
            flash('Acceso restringido a profesores', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def require_alumno(f):
    """Decorador para rutas exclusivas de alumnos"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('tipo_usuario') != 'alumno' or 'alumno_id' not in session:
            flash('Debes iniciar sesión como alumno', 'danger')
            return redirect(url_for('login_alumnos'))
        return f(*args, **kwargs)
    return decorated_function

def require_any_auth(f):
    """Decorador para rutas que requieren cualquier tipo de autenticación"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'tipo_usuario' not in session:
            flash('Debes iniciar sesión para acceder a esta página', 'danger')
            if request.path.startswith('/admin'):
                return redirect(url_for('login'))
            else:
                return redirect(url_for('login_alumnos'))
        return f(*args, **kwargs)
    return decorated_function

# 🆕 RENOVAR SESIÓN EN CADA REQUEST
@app.before_request
def renovar_sesion():
    """Marca la sesión como permanente y la renueva en cada petición"""
    session.permanent = True
    session.modified = True
    
# --- CONFIGURACIÓN DE BASE DE DATOS ---
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///escuela.db')

# Si viene de Heroku/Render con postgres://, cambiar a postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CONFIGURACIÓN DEL POOL DE CONEXIONES (EVITA ERRORES SSL EOF)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,      # Verifica conexión antes de usarla
    'pool_recycle': 300,         # Recicla conexiones cada 5 minutos
    'pool_size': 10,             # Tamaño del pool
    'max_overflow': 20           # Conexiones extras permitidas
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
    entregas = db.relationship('EntregaAlumno', backref='alumno', lazy=True)

class EntregaAlumno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'), nullable=False)
    nombre_alumno = db.Column(db.String(100))
    grado_grupo = db.Column(db.String(20))
    archivo_url = db.Column(db.String(300))
    estrellas = db.Column(db.Integer, default=0)
    comentarios = db.Column(db.Text)
    fecha_entrega = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================
# SOLUCIÓN 1: MODELO ASISTENCIA CORREGIDO
# ============================================
class Asistencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    fecha = db.Column(db.Date, default=datetime.utcnow)
    estado = db.Column(db.String(10))
    alumno = db.relationship('UsuarioAlumno', backref=db.backref('asistencias', lazy=True, cascade='all, delete-orphan'))

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
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'))
    nombre_alumno = db.Column(db.String(100))
    grado_grupo = db.Column(db.String(20))
    contenido = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

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
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'), nullable=False)
    archivo_url = db.Column(db.String(500))
    nombre_archivo = db.Column(db.String(200))
    fecha_generacion = db.Column(db.DateTime, default=datetime.utcnow)
    periodo = db.Column(db.String(50))
    promedio = db.Column(db.Float)
    observaciones = db.Column(db.Text)
    generado_por = db.Column(db.String(100))
    alumno = db.relationship('UsuarioAlumno', backref=db.backref('boletas', lazy=True))

# --- FUNCIONES AUXILIARES REFACTORIZADAS ---

def get_current_user():
    """
    Retorna (tipo_usuario, id, datos) o None
    Centraliza la verificación de sesión para mayor seguridad
    """
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
    """
    Verifica si una columna existe en una tabla de forma segura
    Usa SQLAlchemy metadata en lugar de consultas SQL directas
    """
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)

def migrar_bd_fotos():
    """
    Migración segura para agregar columna foto_perfil si no existe
    """
    try:
        if not column_exists('usuario_alumno', 'foto_perfil'):
            logger.info("🔧 Migrando BD: Agregando columna 'foto_perfil'...")
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE usuario_alumno ADD COLUMN foto_perfil VARCHAR(300)"))
                conn.commit()
            logger.info("✅ Migración completada: columna 'foto_perfil' agregada")
        else:
            logger.info("✅ Columna 'foto_perfil' ya existe")
    except Exception as e:
        logger.error(f"⚠️ Error en migración: {str(e)}")
        raise AppError(f"Error en migración: {str(e)}")

def guardar_archivo(archivo):
    """
    Guarda archivo en S3 si hay credenciales, sino en carpeta local 'uploads'.
    Retorna: Una tupla (ruta_s3_key_o_filename, es_s3)
    """
    filename = secure_filename(archivo.filename)
    
    logger.info(f"Intentando guardar archivo: {filename}")
    
    try:
        # Validar archivo
        file_validator.validate(archivo, filename)
        
        # Intento de S3
        if s3_manager.is_configured:
            try:
                logger.info(f"☁️ Intentando subir a iDrive e2...")
                
                # Detectar tipo de contenido
                content_type = archivo.content_type or 'application/octet-stream'
                s3_key = f"uploads/{filename}"
                
                # Subir a S3
                archivo.seek(0)
                s3_manager.upload_file(archivo, s3_key, content_type)
                
                logger.info(f"✅ Archivo subido exitosamente a S3: {s3_key}")
                return (s3_key, True)
                
            except S3UploadError as e:
                logger.warning(f"❌ Error al subir a S3: {str(e)}")
                logger.info("💾 Guardando localmente como fallback...")
                flash('Advertencia: No se pudo subir a la nube. Guardado localmente.', 'warning')
        else:
            logger.info("⚠️ Credenciales S3 incompletas. Guardando localmente...")
        
        # Fallback Local
        archivo.seek(0)
        local_path = os.path.join(UPLOAD_FOLDER, filename)
        archivo.save(local_path)
        logger.info(f"💾 Archivo guardado localmente: {filename}")
        
        return (filename, False)
        
    except FileValidationError as e:
        logger.error(f"Validación de archivo falló: {str(e)}")
        raise FileValidationError(str(e))
    except Exception as e:
        logger.error(f"Error general al guardar archivo: {str(e)}")
        raise AppError(f"Error al guardar archivo: {str(e)}")

def generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin=None):
    """
    Genera un PDF con el reporte de asistencia y lo guarda en S3
    Retorna: (url_donde_se_guardo, buffer_pdf, nombre_archivo)
    """
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
        
        if fecha_fin:
            periodo = f"Período: {fecha_inicio} a {fecha_fin}"
        else:
            periodo = f"Fecha: {fecha_inicio}"
        
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
        
        if isinstance(fecha_inicio, str):
            fecha_inicio_obj = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
        else:
            fecha_inicio_obj = fecha_inicio
        
        alumnos = UsuarioAlumno.query.filter_by(grado_grupo=grupo).all()
        
        data = [['#', 'Nombre del Alumno', 'Presente', 'Falta', 'Retardo', 'Justificado', 'Total']]
        
        for idx, alumno in enumerate(alumnos, 1):
            query = Asistencia.query.filter_by(alumno_id=alumno.id)
            
            if fecha_fin:
                fecha_fin_obj = datetime.strptime(fecha_fin, '%Y-%m-%d').date() if isinstance(fecha_fin, str) else fecha_fin
                query = query.filter(Asistencia.fecha >= fecha_inicio_obj, Asistencia.fecha <= fecha_fin_obj)
            else:
                query = query.filter_by(fecha=fecha_inicio_obj)
            
            presentes = query.filter_by(estado='P').count()
            faltas = query.filter_by(estado='F').count()
            retardos = query.filter_by(estado='R').count()
            justificados = query.filter_by(estado='J').count()
            total = presentes + faltas + retardos + justificados
            
            data.append([
                str(idx),
                alumno.nombre_completo,
                str(presentes),
                str(faltas),
                str(retardos),
                str(justificados),
                str(total)
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
        
        total_alumnos = len(alumnos)
        total_registros = sum([
            Asistencia.query.filter_by(alumno_id=a.id).filter(
                Asistencia.fecha >= fecha_inicio_obj
            ).count() for a in alumnos
        ])
        
        stats_text = f"""
        <b>Resumen del Grupo:</b><br/>
        Total de alumnos: {total_alumnos}<br/>
        Total de registros de asistencia: {total_registros}
        """
        
        stats_style = ParagraphStyle(
            'Stats',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=20
        )
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
                logger.info(f"☁️ Guardando PDF en iDrive e2: {filename}")
                buffer_copy = BytesIO(buffer.getvalue())
                s3_key = f"reportes/{filename}"
                file_url = s3_manager.upload_file(buffer_copy, s3_key, 'application/pdf')
                logger.info(f"✅ PDF guardado en iDrive e2")
            except S3UploadError as e:
                logger.warning(f"⚠️ No se pudo guardar en iDrive e2: {str(e)}")
        
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'reportes'), exist_ok=True)
        local_path = os.path.join(UPLOAD_FOLDER, 'reportes', filename)
        
        with open(local_path, 'wb') as f:
            f.write(buffer.getvalue())
        
        logger.info(f"💾 PDF guardado localmente como respaldo")
        
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
        logger.info(f"📝 Reporte registrado en base de datos")
        
        buffer.seek(0)
        return (file_url, buffer, filename)
        
    except Exception as e:
        logger.error(f"Error al generar PDF de asistencia: {str(e)}")
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
                logger.info(f"✅ Boleta guardada en iDrive e2: {filename}")
            except S3UploadError as e:
                logger.warning(f"⚠️ Error S3: {e}")
        
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'boletas'), exist_ok=True)
        with open(os.path.join(UPLOAD_FOLDER, 'boletas', filename), 'wb') as f:
            f.write(buffer.getvalue())
        
        buffer.seek(0)
        return (file_url or f"boletas/{filename}", buffer, filename)
        
    except Exception as e:
        logger.error(f"Error al generar PDF de boleta: {str(e)}")
        raise AppError(f"Error al generar boleta: {str(e)}")

# --- HANDLERS DE ERROR ---

@app.errorhandler(404)
def not_found_error(error):
    logger.warning(f"404 Error: {request.url}")
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"500 Error: {str(error)}")
    return render_template('errors/500.html'), 500

@app.errorhandler(AppError)
def app_error(error):
    logger.error(f"AppError: {str(error)}")
    flash(f'Error en la aplicación: {str(error)}', 'danger')
    return redirect(url_for('index'))

@app.errorhandler(FileValidationError)
def file_validation_error(error):
    logger.warning(f"FileValidationError: {str(error)}")
    flash(f'Error de validación de archivo: {str(error)}', 'danger')
    return redirect(request.referrer or url_for('index'))

@app.errorhandler(S3UploadError)
def s3_upload_error(error):
    logger.error(f"S3UploadError: {str(error)}")
    flash(f'Error al subir a la nube: {str(error)}', 'danger')
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
        
        if usuario == 'admin' and token == TOKEN_MAESTRO:
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
    session.pop('alumno_id', None)
    session.pop('alumno_nombre', None)
    session.pop('alumno_grado', None)
    session.pop('alumno_username', None)
    session.pop('tipo_usuario', None)
    return redirect(url_for('index'))

@app.route('/alumnos/perfil/foto', methods=['POST'])
@require_alumno
@limiter.limit("5 per hour")  # Máx 5 cambios de foto por hora
def actualizar_foto_perfil():
    if 'foto' not in request.files:
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    foto = request.files['foto']
    
    if foto.filename == '':
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    try:
        ruta_foto, es_s3 = guardar_archivo(foto)
        alumno = UsuarioAlumno.query.get(session['alumno_id'])
        alumno.foto_perfil = ruta_foto
        db.session.commit()
        
        flash('¡Foto de perfil actualizada correctamente! 🎉', 'success')
        
    except (FileValidationError, AppError) as e:
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
@limiter.limit("30 per minute")  # Máx 30 mensajes por minuto por alumno
def enviar_mensaje():
    config = Configuracion.query.get('chat_activo')
    if config and config.valor == 'False':
        return {'status': 'error', 'msg': 'Chat desactivado por el profesor'}, 403

    contenido = request.form.get('mensaje')
    if not contenido or contenido.strip() == '':
        return {'status': 'error', 'msg': 'Mensaje vacío'}, 400
    
    if len(contenido.strip()) > 500:
        return {'status': 'error', 'msg': 'Mensaje demasiado largo (máx 500 caracteres)'}, 400

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
    
    # ✅ OPTIMIZACIÓN CRÍTICA: Obtener solo los últimos 50 mensajes
    mensajes = Mensaje.query.filter_by(grado_grupo=mi_grupo)\
        .order_by(Mensaje.fecha.desc())\
        .limit(50)\
        .all()
    
    # Revertir para tener orden cronológico
    mensajes.reverse()
    
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
        'activo': chat_activo,
        'total_mensajes': len(lista_mensajes)
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
                         fecha_hoy=date.today().isoformat())

@app.route('/admin/alumnos/agregar', methods=['POST'])
@require_profesor
@limiter.limit("20 per hour")  # Máx 20 alumnos por hora
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
@limiter.limit("30 per hour")  # Máx 30 ediciones por hora
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
@limiter.limit("10 per hour")  # Máx 10 eliminaciones por hora
def eliminar_alumno(id):
    alumno = UsuarioAlumno.query.get_or_404(id)
    nombre = alumno.nombre_completo
    db.session.delete(alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre} eliminado del sistema.', 'warning')
    return redirect(url_for('gestionar_alumnos'))

# ============================================
# SOLUCIÓN 2: RUTA TOMAR ASISTENCIA CORREGIDA
# ============================================
@app.route('/admin/asistencia/tomar', methods=['POST'])
@require_profesor
@limiter.limit("10 per minute")
def tomar_asistencia():
    fecha_str = request.form.get('fecha', datetime.utcnow().strftime('%Y-%m-%d'))
    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    
    logger.info(f"📝 Tomando asistencia para fecha: {fecha_str}")
    
    # Contador para debug
    registros_procesados = 0
    registros_creados = 0
    registros_actualizados = 0
    
    for key, value in request.form.items():
        if key.startswith('asistencia_'):
            try:
                # Extraer el ID del alumno correctamente
                alumno_id = int(key.split('_')[1])
                estado = value
                
                logger.info(f"Procesando: alumno_id={alumno_id}, estado={estado}")
                
                # Verificar que el alumno existe
                alumno = UsuarioAlumno.query.get(alumno_id)
                if not alumno:
                    logger.warning(f"⚠️ Alumno ID {alumno_id} no encontrado, saltando...")
                    continue
                
                # Buscar registro existente
                registro = Asistencia.query.filter_by(
                    alumno_id=alumno_id, 
                    fecha=fecha_obj
                ).first()
                
                if registro:
                    # Actualizar registro existente
                    registro.estado = estado
                    registros_actualizados += 1
                    logger.info(f"✏️ Actualizado: {alumno.nombre_completo} -> {estado}")
                else:
                    # Crear nuevo registro
                    nuevo = Asistencia(
                        alumno_id=alumno_id, 
                        fecha=fecha_obj, 
                        estado=estado
                    )
                    db.session.add(nuevo)
                    registros_creados += 1
                    logger.info(f"➕ Creado: {alumno.nombre_completo} -> {estado}")
                
                registros_procesados += 1
                
            except ValueError as e:
                logger.error(f"❌ Error parseando ID de '{key}': {str(e)}")
                continue
            except Exception as e:
                logger.error(f"❌ Error procesando '{key}': {str(e)}")
                continue
    
    try:
        db.session.commit()
        logger.info(f"✅ Asistencia guardada: {registros_procesados} procesados, {registros_creados} nuevos, {registros_actualizados} actualizados")
        flash(f'✅ Asistencia del día {fecha_str} guardada correctamente. ({registros_procesados} registros)', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error al guardar asistencia: {str(e)}")
        flash(f'❌ Error al guardar asistencia: {str(e)}', 'danger')
    
    return redirect(url_for('gestionar_alumnos', grado=request.form.get('grado_origen')))

@app.route('/admin/reporte-asistencia/<grupo>')
@require_profesor
@limiter.limit("5 per minute")  # Máx 5 reportes por minuto
def generar_reporte_asistencia(grupo):
    fecha_inicio = request.args.get('fecha_inicio', date.today().isoformat())
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
        logger.error(f"Error al generar reporte: {str(e)}")
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
        logger.error(f"Error al descargar reporte: {str(e)}")
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
@limiter.limit("30 per minute")  # Máx 30 calificaciones por minuto
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
            primer_dia = date(anio, mes, 1)
            if mes == 12:
                ultimo_dia = date(anio + 1, 1, 1) - timedelta(days=1)
            else:
                ultimo_dia = date(anio, mes + 1, 1) - timedelta(days=1)
            
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
        ReporteAsistencia.fecha_generacion >= date.today().replace(day=1)
    ).count()
    
    return render_template('admin/reportes_asistencia.html',
                         reportes=reportes,
                         grupos_disponibles=grupos_disponibles,
                         filtro_grupo=filtro_grupo,
                         filtro_mes=filtro_mes,
                         filtro_anio=filtro_anio,
                         total_reportes=total_reportes,
                         reportes_este_mes=reportes_este_mes,
                         fecha_hoy=date.today().isoformat())

@app.route('/admin/descargar-reporte/<int:reporte_id>')
@require_profesor
def descargar_reporte_guardado(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        if reporte.archivo_url and reporte.archivo_url.startswith('http'):
            if s3_manager.is_configured:
                key = f"reportes/{reporte.nombre_archivo}"
                file_stream, content_type = s3_manager.download_file(key)
                
                if file_stream:
                    return send_file(
                        file_stream,
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=reporte.nombre_archivo
                    )
        
        return send_from_directory(
            os.path.join(UPLOAD_FOLDER, 'reportes'),
            reporte.nombre_archivo,
            as_attachment=True
        )
        
    except Exception as e:
        logger.error(f"Error al descargar reporte: {str(e)}")
        flash(f'Error al descargar reporte: {str(e)}', 'danger')
        return redirect(url_for('ver_reportes_asistencia'))

@app.route('/admin/eliminar-reporte/<int:reporte_id>')
@require_profesor
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
def eliminar_reporte(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        if reporte.archivo_url and reporte.archivo_url.startswith('http') and s3_manager.is_configured:
            try:
                key = f"reportes/{reporte.nombre_archivo}"
                s3_manager.delete_file(key)
                logger.info(f"🗑️ Archivo eliminado de S3: {key}")
            except S3UploadError as e:
                logger.warning(f"⚠️ No se pudo eliminar de S3: {e}")
        
        db.session.delete(reporte)
        db.session.commit()
        flash('Reporte eliminado correctamente', 'success')
        
    except Exception as e:
        logger.error(f"Error al eliminar reporte: {str(e)}")
        flash(f'Error al eliminar reporte: {str(e)}', 'danger')
    
    return redirect(url_for('ver_reportes_asistencia'))

# ============================================
# SOLUCIÓN 3: RUTA TEMPORAL PARA DEBUGGING
# ============================================
@app.route('/admin/debug-asistencia', methods=['POST'])
@require_profesor
def debug_asistencia():
    """Ruta temporal para debugging"""
    fecha_str = request.form.get('fecha', datetime.utcnow().strftime('%Y-%m-%d'))
    
    debug_info = {
        'fecha': fecha_str,
        'campos_recibidos': []
    }
    
    for key, value in request.form.items():
        if key.startswith('asistencia_'):
            try:
                alumno_id = int(key.split('_')[1])
                alumno = UsuarioAlumno.query.get(alumno_id)
                
                debug_info['campos_recibidos'].append({
                    'key': key,
                    'alumno_id': alumno_id,
                    'alumno_nombre': alumno.nombre_completo if alumno else "NO ENCONTRADO",
                    'estado': value
                })
            except ValueError:
                debug_info['campos_recibidos'].append({
                    'key': key,
                    'error': 'ID no válido',
                    'estado': value
                })
    
    return jsonify(debug_info)

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
@limiter.limit("10 per hour")  # Máx 10 tareas por hora por alumno
def subir_tarea():
    if 'archivo' not in request.files:
        flash('No se subió archivo', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    archivo = request.files['archivo']
    if archivo.filename == '':
        flash('Ningún archivo seleccionado', 'danger')
        return redirect(url_for('panel_alumnos'))

    if archivo:
        alumno = UsuarioAlumno.query.get(session['alumno_id'])
        
        try:
            ruta, es_s3 = guardar_archivo(archivo)
            
            nueva_entrega = EntregaAlumno(
                alumno_id=alumno.id,
                nombre_alumno=alumno.nombre_completo,
                grado_grupo=alumno.grado_grupo,
                archivo_url=ruta
            )
            db.session.add(nueva_entrega)
            db.session.commit()
            
            flash('¡Tarea enviada con éxito! El profesor la revisará pronto.', 'success')
        except (FileValidationError, AppError) as e:
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
@limiter.limit("20 per hour")  # Máx 20 equipos por hora
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
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
def eliminar_equipo(id):
    equipo = Equipo.query.get_or_404(id)
    db.session.delete(equipo)
    db.session.commit()
    flash('Equipo eliminado del inventario', 'warning')
    return redirect(url_for('inventario'))

@app.route('/admin/generar_qr_img/<int:id>')
@require_profesor
@limiter.limit("30 per minute")  # Máx 30 QR por minuto
def generar_qr_img(id):
    equipo = Equipo.query.get_or_404(id)
    info_qr = f"PROPIEDAD ESCUELA MARIANO ESCOBEDO\nID: {equipo.id}\nTipo: {equipo.tipo}\nMarca: {equipo.marca}\nModelo: {equipo.modelo}"

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(info_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    img_io = io.BytesIO()
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
@limiter.limit("10 per minute")  # Máx 10 reportes por minuto
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
@limiter.limit("10 per minute")  # Máx 10 soluciones por minuto
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
@limiter.limit("10 per hour")  # Máx 10 anuncios por hora
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
@limiter.limit("20 per minute")  # Máx 20 eliminaciones por minuto
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
@limiter.limit("10 per hour")  # Máx 10 cuestionarios por hora
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
@limiter.limit("20 per minute")  # Máx 20 eliminaciones por minuto
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
@limiter.limit("10 per hour")  # Máx 10 añadidos al banco por hora
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
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
def eliminar_del_banco(id):
    item = BancoCuestionario.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Plantilla eliminada.', 'warning')
    return redirect(url_for('gestionar_banco'))

@app.route('/admin/banco/asignar', methods=['POST'])
@require_profesor
@limiter.limit("20 per hour")  # Máx 20 asignaciones por hora
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
            db.session.add(actividad)
        
        actividad.titulo = titulo
        actividad.descripcion = descripcion
        actividad.fecha_actualizacion = datetime.utcnow()
        
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
@limiter.limit("10 per hour")  # Máx 10 horarios por hora
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
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
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
@limiter.limit("10 per hour")  # Máx 10 plataformas por hora
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
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
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
@limiter.limit("10 per hour")  # Máx 10 recursos por hora
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
        except (FileValidationError, AppError) as e:
            flash(f'Error: {str(e)}', 'danger')
            
    return redirect(url_for('gestionar_recursos'))

@app.route('/admin/recursos/eliminar/<int:id>')
@require_profesor
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
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
@limiter.limit("30 per minute")  # Máx 30 descargas por minuto
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
        logger.error(f"Error al servir archivo: {str(e)}")
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
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
def borrar_criterio(id):
    c = CriterioBoleta.query.get_or_404(id)
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for('configurar_boletas'))

@app.route('/admin/boletas/generar', methods=['GET', 'POST'])
@require_profesor
@limiter.limit("5 per minute")  # Máx 5 boletas por minuto
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
            logger.error(f"Error al generar boleta: {str(e)}")
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
        BoletaGenerada.fecha_generacion >= date.today().replace(day=1)
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
    
    try:
        if boleta.archivo_url and boleta.archivo_url.startswith('boletas/'):
            if s3_manager.is_configured:
                file_stream, content_type = s3_manager.download_file(boleta.archivo_url)
                
                if file_stream:
                    return send_file(
                        file_stream,
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=boleta.nombre_archivo
                    )
        
        return send_from_directory(
            os.path.join(UPLOAD_FOLDER, 'boletas'),
            boleta.nombre_archivo,
            as_attachment=True
        )
        
    except Exception as e:
        logger.error(f"Error al descargar boleta: {str(e)}")
        flash(f'Error al descargar boleta: {str(e)}', 'danger')
        return redirect(url_for('ver_boletas_historial'))

@app.route('/admin/boletas/eliminar/<int:boleta_id>')
@require_profesor
@limiter.limit("10 per minute")  # Máx 10 eliminaciones por minuto
def eliminar_boleta_guardada(boleta_id):
    boleta = BoletaGenerada.query.get_or_404(boleta_id)
    
    try:
        if boleta.archivo_url and boleta.archivo_url.startswith('boletas/') and s3_manager.is_configured:
            try:
                s3_manager.delete_file(boleta.archivo_url)
                logger.info(f"🗑️ Boleta eliminada de S3: {boleta.archivo_url}")
            except S3UploadError as e:
                logger.warning(f"⚠️ No se pudo eliminar de S3: {e}")
        
        db.session.delete(boleta)
        db.session.commit()
        flash('Boleta eliminada correctamente', 'success')
        
    except Exception as e:
        logger.error(f"Error al eliminar boleta: {str(e)}")
        flash(f'Error al eliminar boleta: {str(e)}', 'danger')
    
    return redirect(url_for('ver_boletas_historial'))

# --- INICIALIZADOR ---

with app.app_context():
    db.create_all()
    migrar_bd_fotos()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
[file content end]