# web/utils.py
from functools import wraps
from flask import session, flash, redirect, url_for, request, send_file, send_from_directory
import os
import boto3
import magic
import logging
from datetime import datetime, timedelta
import time
from collections import defaultdict
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# Configuración de logging
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

# --- EXCEPCIONES PERSONALIZADAS ---
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

# --- DECORADORES DE SEGURIDAD ---
def require_role(role):
    """Decorador genérico para control de acceso por rol"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('tipo_usuario') != role:
                flash(f'Acceso restringido a {role}s', 'danger')
                return redirect(url_for(f'auth.login_alumnos' if role == 'alumno' else 'auth.login'))
            
            if role == 'profesor' and 'user' not in session:
                flash('Acceso restringido a profesores', 'danger')
                return redirect(url_for('auth.login'))
            
            if role == 'alumno' and 'alumno_id' not in session:
                flash('Debes iniciar sesión como alumno', 'danger')
                return redirect(url_for('auth.login_alumnos'))
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

require_profesor = require_role('profesor')
require_alumno = require_role('alumno')

def require_any_auth(f):
    """Decorador para rutas que requieren cualquier tipo de autenticación"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'tipo_usuario' not in session:
            flash('Debes iniciar sesión para acceder a esta página', 'danger')
            redirect_to = 'auth.login' if request.path.startswith('/admin') else 'auth.login_alumnos'
            return redirect(url_for(redirect_to))
        return f(*args, **kwargs)
    return decorated_function

# --- CLASES DE APOYO ---
class S3Manager:
    """Gestor centralizado para operaciones S3 / iDrive e2"""
    
    def __init__(self, app_config=None):
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
    
    # 🆕 NUEVA FUNCIÓN: Generar URLs firmadas para iDrive e2
    def generate_presigned_url(self, key, expiration=3600):
        """
        Generar URL firmada (presigned URL) para acceso temporal a archivos privados
        
        Args:
            key: La clave/path del archivo en S3
            expiration: Tiempo de expiración en segundos (default: 3600 = 1 hora)
        
        Returns:
            URL firmada temporal que permite acceso al archivo privado
        """
        try:
            client = self.get_client()
            
            url = client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': key
                },
                ExpiresIn=expiration
            )
            
            log_info(f"URL firmada generada para: {key} (expira en {expiration}s)")
            return url
            
        except Exception as e:
            log_error(f"Error al generar URL firmada: {str(e)}")
            raise S3UploadError(f"Error al generar URL firmada: {str(e)}")

class FileValidator:
    """Validador de archivos centralizado"""
    
    def __init__(self):
        self.max_size = 50 * 1024 * 1024  # 50MB
        self.allowed_extensions = {
            'images': {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'},
            'documents': {'pdf', 'doc', 'docx', 'txt', 'odt', 'ppt', 'pptx', 'xls', 'xlsx'},
            'archives': {'zip', 'rar', '7z'}
        }
        self.all_extensions = set().union(*self.allowed_extensions.values())
        
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
            if ext not in self.all_extensions:
                raise FileValidationError(f"Extensión .{ext} no permitida")
                    
        except Exception as e:
            log_warning(f"Magic number validation skipped: {str(e)}")
            # Si falla la validación MIME, al menos validar extensión
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            if ext not in self.all_extensions:
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

class RateLimiter:
    """Limitador de tasa para prevenir abuso"""
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

# --- FUNCIONES AUXILIARES ---
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

def guardar_archivo(archivo, upload_folder='uploads'):
    """Guarda archivo en S3 o localmente con verificación de espacio"""
    import shutil
    
    filename = secure_filename(archivo.filename)
    os.makedirs(upload_folder, exist_ok=True)
    
    log_info(f"Intentando guardar archivo: {filename}")
    
    try:
        # Validar archivo
        validator = FileValidator()
        validator.validate(archivo, filename)
        archivo.seek(0)
        
        # Verificar espacio en disco
        def check_disk_space(min_free_gb=1):
            stat = shutil.disk_usage(upload_folder)
            free_gb = stat.free / (1024**3)
            if free_gb < min_free_gb:
                raise DiskSpaceError(f"Espacio insuficiente. Solo {free_gb:.2f}GB disponibles")
            return True
        
        # Intentar S3 primero
        s3_mgr = S3Manager()
        if s3_mgr.is_configured:
            try:
                content_type = archivo.content_type or 'application/octet-stream'
                s3_key = f"uploads/{filename}"
                s3_mgr.upload_file(archivo, s3_key, content_type)
                return (s3_key, True)
            except S3UploadError as e:
                log_warning(f"Error S3: {e}. Guardando localmente...")
                check_disk_space()
        
        # Guardar localmente
        check_disk_space()
        local_path = os.path.join(upload_folder, filename)
        archivo.save(local_path)
        log_info(f"Archivo guardado localmente: {filename}")
        
        return (filename, False)
        
    except (FileValidationError, DiskSpaceError, S3UploadError) as e:
        raise e
    except Exception as e:
        raise AppError(f"Error al guardar archivo: {str(e)}")

def generar_qr_img(data, fill_color="black", back_color="white"):
    """Genera imagen QR a partir de datos"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color=back_color)
    
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return img_io

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
        
        s3_mgr = S3Manager()
        if s3_mgr.is_configured:
            try:
                s3_key = f"boletas/{filename}"
                file_url = s3_mgr.upload_file(BytesIO(buffer.getvalue()), s3_key, 'application/pdf')
            except S3UploadError as e:
                log_warning(f"Error S3: {e}")
        
        os.makedirs(os.path.join('uploads', 'boletas'), exist_ok=True)
        with open(os.path.join('uploads', 'boletas', filename), 'wb') as f:
            f.write(buffer.getvalue())
        
        buffer.seek(0)
        return (file_url or f"boletas/{filename}", buffer, filename)
        
    except Exception as e:
        log_error(f"Error al generar PDF de boleta: {str(e)}")
        raise AppError(f"Error al generar boleta: {str(e)}")

def generar_recibo_pdf(numero_recibo, pago, monto_pagado, metodo_pago, observaciones, recibido_por):
    """Genera PDF de recibo de pago"""
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        # Estilo del título
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a5490'),
            spaceAfter=20,
            alignment=TA_CENTER
        )
        
        # Título
        elements.append(Paragraph("ESCUELA MARIANO ESCOBEDO", title_style))
        elements.append(Paragraph("Recibo de Pago", styles['Heading2']))
        elements.append(Spacer(1, 20))
        
        # Información del recibo
        info_style = ParagraphStyle(
            'Info',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=10,
            alignment=TA_LEFT
        )
        
        info_text = f"""
        <b>Recibo No:</b> {numero_recibo}<br/>
        <b>Fecha:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<br/>
        <b>Alumno:</b> {pago.alumno.nombre_completo}<br/>
        <b>Grado/Grupo:</b> {pago.alumno.grado_grupo}<br/>
        <b>Concepto:</b> {pago.concepto}<br/>
        """
        
        elements.append(Paragraph(info_text, info_style))
        elements.append(Spacer(1, 20))
        
        # Tabla de montos
        data = [
            ['Concepto', 'Monto'],
            ['Monto Total del Pago', f'${pago.monto_total:,.2f}'],
            ['Monto Pagado Anteriormente', f'${pago.monto_pagado - monto_pagado:,.2f}'],
            ['Pago Actual', f'${monto_pagado:,.2f}'],
            ['Monto Pendiente', f'${pago.monto_pendiente - monto_pagado:,.2f}'],
        ]
        
        tabla = Table(data, colWidths=[4*inch, 2*inch])
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5490')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BACKGROUND', (0, 3), (-1, 3), colors.HexColor('#90EE90')),
            ('FONTNAME', (0, 3), (-1, 3), 'Helvetica-Bold'),
        ]))
        
        elements.append(tabla)
        elements.append(Spacer(1, 20))
        
        # Método de pago
        metodo_text = f"<b>Método de Pago:</b> {metodo_pago}"
        elements.append(Paragraph(metodo_text, info_style))
        elements.append(Spacer(1, 10))
        
        # Observaciones
        if observaciones:
            obs_text = f"<b>Observaciones:</b><br/>{observaciones}"
            elements.append(Paragraph(obs_text, info_style))
            elements.append(Spacer(1, 20))
        
        # Recibido por
        recibido_text = f"<b>Recibido por:</b> {recibido_por}"
        elements.append(Paragraph(recibido_text, info_style))
        elements.append(Spacer(1, 40))
        
        # Firmas
        firma_tabla = Table([
            ['_________________________', '_________________________'],
            ['Firma del Padre/Tutor', 'Firma del Receptor']
        ], colWidths=[3*inch, 3*inch])
        
        firma_tabla.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 20),
        ]))
        
        elements.append(firma_tabla)
        
        # Construir PDF
        doc.build(elements)
        buffer.seek(0)
        
        return buffer
        
    except Exception as e:
        log_error(f"Error al generar PDF de recibo: {str(e)}")
        raise AppError(f"Error al generar recibo: {str(e)}")

def descargar_archivo(archivo_url, nombre_archivo, carpeta_local):
    """Función helper para descargar archivos desde S3 o local"""
    s3_mgr = S3Manager()
    
    if archivo_url and (archivo_url.startswith('http') or 'uploads/' in archivo_url):
        if s3_mgr.is_configured:
            file_stream, content_type = s3_mgr.download_file(archivo_url)
            return send_file(file_stream, mimetype=content_type, 
                           as_attachment=True, download_name=nombre_archivo)
    
    return send_from_directory(os.path.join('uploads', carpeta_local), 
                              nombre_archivo, as_attachment=True)

# --- INSTANCIAS GLOBALES ---
s3_manager = S3Manager()
file_validator = FileValidator()
chat_limiter = RateLimiter(max_requests=10, window_seconds=60)  # Para limitar mensajes de chat