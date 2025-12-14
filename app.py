import os
import boto3
import qrcode
import io  # Para manejar archivos en memoria
from datetime import datetime, timedelta, date  # Añadido timedelta aquí
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# --- NUEVOS IMPORTS PARA GENERAR PDF ---
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
app.secret_key = 'clave_secreta_desarrollo'  # Cambiar en producción

# NUEVO: La sesión expira tras 10 minutos de inactividad
app.permanent_session_lifetime = timedelta(minutes=10)

# --- CONFIGURACIÓN DE BASE DE DATOS ---

# Obtener la URL de la variable de entorno
database_url = os.environ.get('DATABASE_URL', 'sqlite:///taller.db')

# FIX PARA RENDER/NEON: 
# Si la URL comienza con "postgres://", la cambiamos a "postgresql://" 
# porque SQLAlchemy moderno ya no acepta la versión corta.
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 2. Configuración de Archivos (S3 / Local)
# Intenta leer credenciales de variables de entorno
S3_ENDPOINT = os.environ.get('S3_ENDPOINT') 
S3_KEY = os.environ.get('S3_KEY')
S3_SECRET = os.environ.get('S3_SECRET')
S3_BUCKET = os.environ.get('S3_BUCKET_NAME', 'taller-computo')

# Carpeta local de respaldo si no hay S3
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Crea la carpeta si no existe

# Debug: Imprimir configuración al iniciar
print("=" * 50)
print("CONFIGURACIÓN S3/iDrive e2:")
print(f"S3_ENDPOINT: {S3_ENDPOINT}")
print(f"S3_BUCKET: {S3_BUCKET}")
print(f"S3_KEY configurado: {bool(S3_KEY)}")
print(f"S3_SECRET configurado: {bool(S3_SECRET)}")
print("=" * 50)

# --- MODELOS DE LA BASE DE DATOS ---

class Equipo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50)) # PC, Monitor, Teclado, etc.
    marca = db.Column(db.String(50))
    modelo = db.Column(db.String(50))
    estado = db.Column(db.String(20), default='Funcional') # Funcional, En Reparación, Baja
    qr_data = db.Column(db.String(200)) # Url o texto del QR

class Mantenimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    equipo_id = db.Column(db.Integer, db.ForeignKey('equipo.id'))
    descripcion_falla = db.Column(db.Text)
    fecha_reporte = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_reparacion = db.Column(db.DateTime, nullable=True)
    solucion = db.Column(db.Text, nullable=True)
    
    # ESTA LÍNEA ES NUEVA: Nos permite acceder a los datos del equipo desde el reporte
    equipo = db.relationship('Equipo', backref=db.backref('mantenimientos', lazy=True))

class Anuncio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100))
    contenido = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class UsuarioAlumno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)  # Ejemplo: 6AMatias2007
    nombre_completo = db.Column(db.String(100), nullable=False)
    grado_grupo = db.Column(db.String(20), nullable=False)  # Ejemplo: 6A
    password_hash = db.Column(db.String(200), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    activo = db.Column(db.Boolean, default=True)
    
    # Relación con entregas
    entregas = db.relationship('EntregaAlumno', backref='alumno', lazy=True)

class EntregaAlumno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'), nullable=False)
    nombre_alumno = db.Column(db.String(100))
    grado_grupo = db.Column(db.String(20))
    archivo_url = db.Column(db.String(300)) # Ruta local o URL de S3
    estrellas = db.Column(db.Integer, default=0) # Calificación 1-5
    comentarios = db.Column(db.Text)
    fecha_entrega = db.Column(db.DateTime, default=datetime.utcnow)

# --- NUEVO MODELO PARA ASISTENCIA ---
class Asistencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'), nullable=False)
    fecha = db.Column(db.Date, default=datetime.utcnow)
    estado = db.Column(db.String(10)) # 'P'=Presente, 'F'=Falta, 'R'=Retardo, 'J'=Justificado
    
    # Relación para saber de quién es la asistencia
    alumno = db.relationship('UsuarioAlumno', backref=db.backref('asistencias', lazy=True))

# --- MODELO PARA REGISTRO DE REPORTES GENERADOS ---
class ReporteAsistencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grupo = db.Column(db.String(20), nullable=False)  # Ej: "6A"
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date, nullable=True)  # Puede ser None si es solo un día
    fecha_generacion = db.Column(db.DateTime, default=datetime.utcnow)
    archivo_url = db.Column(db.String(500))  # URL de S3 o ruta local
    nombre_archivo = db.Column(db.String(200))
    generado_por = db.Column(db.String(100))  # Username del profesor
    total_alumnos = db.Column(db.Integer, default=0)
    total_registros = db.Column(db.Integer, default=0)

# --- MODELO PARA ACTIVIDADES POR GRADO ---
class ActividadGrado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grado = db.Column(db.Integer) # 1, 2, 3, 4, 5, 6
    titulo = db.Column(db.String(100))
    descripcion = db.Column(db.Text)
    # Opcional: Link a un recurso o foto
    imagen_url = db.Column(db.String(200), nullable=True) 
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow)

# --- MODELO PARA CUESTIONARIOS ---
class Cuestionario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100))
    url = db.Column(db.String(500)) # El link de Google Forms
    grado = db.Column(db.String(20)) # Para quién es: "1°", "2°", etc.
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

# --- MODELO PARA HORARIOS ---
class Horario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dia = db.Column(db.String(20))     # Lunes, Martes...
    grados = db.Column(db.String(50))  # Ej: "1° y 2°"
    hora = db.Column(db.String(50))    # Ej: "08:00 - 10:00 AM"

# --- NUEVO MODELO PARA PLATAFORMAS ---
class Plataforma(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50))
    url = db.Column(db.String(500))
    icono = db.Column(db.String(50)) # Guardaremos la clase de FontAwesome (ej: 'fa-code')

# --- MODELOS PARA EL CHAT ---

class Mensaje(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id'))
    nombre_alumno = db.Column(db.String(100)) # Guardamos el nombre para no hacer tantas consultas
    grado_grupo = db.Column(db.String(20))    # Para filtrar: "6A" solo lee "6A"
    contenido = db.Column(db.Text)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

class Configuracion(db.Model):
    # Una tabla simple para guardar ajustes globales (como el switch del chat)
    clave = db.Column(db.String(50), primary_key=True) # Ej: "chat_activo"
    valor = db.Column(db.String(50)) # Ej: "True" o "False"

# --- FUNCIONES AUXILIARES (HELPER FUNCTIONS) ---

def guardar_archivo(archivo):
    """
    Guarda archivo en S3 si hay credenciales, sino en carpeta local 'uploads'.
    Retorna: El nombre del archivo o URL para guardar en DB.
    """
    filename = secure_filename(archivo.filename)
    
    # Debug: Verificar configuración
    print(f"\n🔍 Intentando guardar archivo: {filename}")
    print(f"   S3_ENDPOINT configurado: {bool(S3_ENDPOINT)}")
    print(f"   S3_KEY configurado: {bool(S3_KEY)}")
    print(f"   S3_SECRET configurado: {bool(S3_SECRET)}")
    
    # Intento de S3
    if S3_ENDPOINT and S3_KEY and S3_SECRET:
        try:
            print(f"   ☁️  Intentando subir a iDrive e2...")
            s3 = boto3.client('s3', 
                            endpoint_url=S3_ENDPOINT,
                            aws_access_key_id=S3_KEY,
                            aws_secret_access_key=S3_SECRET,
                            region_name='us-west-1')  # Región agregada
            
            # Reiniciar el puntero del archivo
            archivo.seek(0)
            
            s3.upload_fileobj(archivo, S3_BUCKET, filename)
            
            # URL pública del archivo
            file_url = f"{S3_ENDPOINT}/{S3_BUCKET}/{filename}"
            print(f"   ✅ Archivo subido exitosamente a S3")
            print(f"   🔗 URL: {file_url}")
            
            return file_url
            
        except Exception as e:
            print(f"   ❌ Error al subir a S3: {str(e)}")
            print(f"   📁 Guardando localmente como fallback...")
            flash(f'Advertencia: No se pudo subir a la nube. Guardado localmente.', 'warning')
    else:
        print(f"   ⚠️  Credenciales S3 incompletas. Guardando localmente...")
    
    # Fallback Local
    archivo.seek(0)  # Reiniciar puntero
    archivo.save(os.path.join(UPLOAD_FOLDER, filename))
    print(f"   💾 Archivo guardado localmente: {filename}")
    return filename

def generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin=None):
    """
    Genera un PDF con el reporte de asistencia y lo guarda en S3
    Retorna: (url_donde_se_guardo, buffer_pdf, nombre_archivo)
    """
    # Buffer en memoria para el PDF
    buffer = BytesIO()
    
    # Crear el documento PDF
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    # Estilo personalizado para el título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a5490'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    # Título del reporte
    titulo = f"Reporte de Asistencia - Grupo {grupo}"
    elements.append(Paragraph(titulo, title_style))
    elements.append(Spacer(1, 12))
    
    # Información de fechas
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
    
    # Obtener datos de asistencia
    if isinstance(fecha_inicio, str):
        fecha_inicio_obj = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
    else:
        fecha_inicio_obj = fecha_inicio
    
    # Buscar alumnos del grupo
    alumnos = UsuarioAlumno.query.filter_by(grado_grupo=grupo).all()
    
    # Crear tabla de datos
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
    
    # Crear la tabla
    tabla = Table(data, colWidths=[0.5*inch, 3*inch, 0.8*inch, 0.8*inch, 0.8*inch, 1*inch, 1*inch])
    
    # Estilo de la tabla
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
    
    # Generar el PDF en memoria
    doc.build(elements)
    buffer.seek(0)
    
    # Nombre del archivo
    fecha_str = fecha_inicio_obj.strftime('%Y%m%d')
    if fecha_fin:
        fecha_fin_str = datetime.strptime(fecha_fin, '%Y-%m-%d').strftime('%Y%m%d') if isinstance(fecha_fin, str) else fecha_fin.strftime('%Y%m%d')
        filename = f"asistencia_{grupo}_{fecha_str}_a_{fecha_fin_str}.pdf"
    else:
        filename = f"asistencia_{grupo}_{fecha_str}.pdf"
    
    # 🔥 CLAVE: Guardar en S3 pero SIN redirigir al usuario
    file_url = None
    if S3_ENDPOINT and S3_KEY and S3_SECRET:
        try:
            print(f"☁️ Guardando PDF en iDrive e2: {filename}")
            s3 = boto3.client('s3',
                            endpoint_url=S3_ENDPOINT,
                            aws_access_key_id=S3_KEY,
                            aws_secret_access_key=S3_SECRET,
                            region_name='us-west-1')
            
            # Crear copia del buffer para subir a S3
            buffer_copy = BytesIO(buffer.getvalue())
            s3.upload_fileobj(buffer_copy, S3_BUCKET, f"reportes/{filename}")
            
            file_url = f"{S3_ENDPOINT}/{S3_BUCKET}/reportes/{filename}"
            print(f"✅ PDF guardado en iDrive e2")
            
        except Exception as e:
            print(f"⚠️ No se pudo guardar en iDrive e2: {str(e)}")
    
    # También guardar localmente como respaldo
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'reportes'), exist_ok=True)
    local_path = os.path.join(UPLOAD_FOLDER, 'reportes', filename)
    
    with open(local_path, 'wb') as f:
        f.write(buffer.getvalue())
    
    print(f"💾 PDF guardado localmente como respaldo")
    
    # 🆕 REGISTRAR EL REPORTE EN LA BASE DE DATOS
    try:
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
        print(f"📝 Reporte registrado en base de datos")
    except Exception as e:
        print(f"⚠️ Error al registrar reporte en BD: {str(e)}")
        # No afecta la generación del PDF
    
    # 🔥 IMPORTANTE: Devolver el buffer para descarga inmediata
    buffer.seek(0)  # Resetear el puntero al inicio
    return (file_url, buffer, filename)

# --- RUTAS PRINCIPALES ---

@app.route('/')
def index():
    # 1. Obtener Anuncios
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(5).all()
    
    # 2. Obtener Horarios (Ordenados por día es difícil con texto, los mostraremos como se creen)
    # Opcional: Podríamos ordenarlos por ID para que salgan en el orden que los agregaste
    horarios = Horario.query.all()
    
    # 3. Obtener Plataformas
    plataformas = Plataforma.query.all()

    return render_template('index.html', anuncios=anuncios, horarios=horarios, plataformas=plataformas)

# --- RUTAS DE AUTENTICACIÓN (LOGIN PROFESOR) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # USUARIO Y CONTRASEÑA "QUEMADOS" PARA USO LOCAL (SIMPLE)
        # Puedes cambiar 'admin' y 'profesor123' por lo que quieras
        if username == 'admin' and password == 'profesor123':
            session.permanent = True  # <--- LÍNEA AGREGADA
            session['user'] = username
            session['tipo_usuario'] = 'profesor'
            flash('¡Bienvenido, Profesor!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Credenciales incorrectas', 'danger')
            return redirect(url_for('login'))
            
    return render_template('admin/login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente.')
    return redirect(url_for('index'))

# --- RUTAS DE ALUMNOS (LOGIN CON USUARIO/CONTRASEÑA) ---

@app.route('/alumnos/login', methods=['GET', 'POST'])
def login_alumnos():
    # Si ya entró, lo mandamos directo al panel
    if 'alumno_id' in session:
        return redirect(url_for('panel_alumnos'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Buscar usuario en la base de datos
        alumno = UsuarioAlumno.query.filter_by(username=username, activo=True).first()
        
        if alumno and check_password_hash(alumno.password_hash, password):
            # Guardar datos en sesión
            session.permanent = True  # <--- LÍNEA AGREGADA
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
    # Borramos solo los datos del alumno
    session.pop('alumno_id', None)
    session.pop('alumno_nombre', None)
    session.pop('alumno_grado', None)
    session.pop('alumno_username', None)
    session.pop('tipo_usuario', None)
    return redirect(url_for('index'))

# --- RUTAS DE ADMINISTRACIÓN ---

@app.route('/admin')
def admin_dashboard():
    # VERIFICACIÓN DE SEGURIDAD: Si no está logueado como profesor, mandar al login
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))

    equipos = Equipo.query.count()
    pendientes = Mantenimiento.query.filter_by(fecha_reparacion=None).count()
    alumnos_activos = UsuarioAlumno.query.filter_by(activo=True).count()
    total_entregas = EntregaAlumno.query.count()
    
    # Verificar estado del chat
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False
    
    return render_template('admin/dashboard.html', 
                         total_equipos=equipos, 
                         reparaciones=pendientes,
                         alumnos_activos=alumnos_activos,
                         total_entregas=total_entregas,
                         chat_activo=chat_activo)

# --- RUTAS DEL CHAT (SISTEMA DE MENSAJERÍA) ---

# 1. INTERRUPTOR DEL PROFE
@app.route('/admin/chat/toggle')
def toggle_chat():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Buscar la configuración, si no existe la creamos
    config = Configuracion.query.get('chat_activo')
    if not config:
        config = Configuracion(clave='chat_activo', valor='True')
        db.session.add(config)
    
    # Invertir el valor (Si es True pasa a False y viceversa)
    if config.valor == 'True':
        config.valor = 'False'
        flash('Chat desactivado para todos los alumnos.', 'secondary')
    else:
        config.valor = 'True'
        flash('Chat activado. Los alumnos pueden conversar.', 'success')
    
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

# 2. ENVIAR MENSAJE (ALUMNO)
@app.route('/api/chat/enviar', methods=['POST'])
def enviar_mensaje():
    if 'alumno_id' not in session:
        return {'status': 'error', 'msg': 'No logueado'}, 403
    
    # Verificar si el chat está activo
    config = Configuracion.query.get('chat_activo')
    if config and config.valor == 'False':
        return {'status': 'error', 'msg': 'Chat desactivado por el profesor'}, 403

    contenido = request.form.get('mensaje')
    if not contenido or contenido.strip() == '':
        return {'status': 'error', 'msg': 'Mensaje vacío'}, 400

    # Guardar mensaje
    nuevo = Mensaje(
        alumno_id=session['alumno_id'],
        nombre_alumno=session['alumno_nombre'],
        grado_grupo=session['alumno_grado'], # Ej: "6A"
        contenido=contenido
    )
    db.session.add(nuevo)
    db.session.commit()
    
    return {'status': 'ok'}

# 3. LEER MENSAJES (ALUMNO)
@app.route('/api/chat/obtener')
def obtener_mensajes():
    if 'alumno_id' not in session:
        return {'status': 'error'}, 403

    # Obtener mensajes SOLO de mi grupo (últimos 50)
    mi_grupo = session['alumno_grado']
    mensajes = Mensaje.query.filter_by(grado_grupo=mi_grupo).order_by(Mensaje.fecha.asc()).all()
    
    # Verificar estado del chat
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False

    # Convertir a JSON para que Javascript lo entienda
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
def gestionar_alumnos():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # 1. Capturar filtro
    filtro = request.args.get('grado') # Ej: '6A'
    
    # 2. Lógica de filtrado
    if filtro and filtro != 'Todos':
        alumnos = UsuarioAlumno.query.filter_by(grado_grupo=filtro).order_by(UsuarioAlumno.nombre_completo).all()
    else:
        alumnos = UsuarioAlumno.query.order_by(UsuarioAlumno.grado_grupo, UsuarioAlumno.nombre_completo).all()
    
    # 3. Estadísticas
    total_alumnos = UsuarioAlumno.query.count()
    alumnos_activos = UsuarioAlumno.query.filter_by(activo=True).count()
    
    # 4. Enviar todo a la plantilla
    return render_template('admin/alumnos.html', 
                         alumnos=alumnos, 
                         total_alumnos=total_alumnos,
                         alumnos_activos=alumnos_activos,
                         filtro_actual=filtro,
                         fecha_hoy=date.today().isoformat())  # ← AGREGAR ESTA LÍNEA

@app.route('/admin/alumnos/agregar', methods=['POST'])
def agregar_alumno():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    username = request.form['username']
    nombre_completo = request.form['nombre_completo']
    password = request.form['password']
    
    # NUEVA LÓGICA: Recibimos grado y grupo por separado y los unimos
    grado = request.form['grado'] # Ej: "6"
    grupo = request.form['grupo'] # Ej: "A"
    grado_grupo = f"{grado}{grupo}" # Resultado: "6A"
    
    # Verificar si el username ya existe
    existe = UsuarioAlumno.query.filter_by(username=username).first()
    if existe:
        flash(f'El usuario "{username}" ya existe. Elige otro.', 'danger')
        return redirect(url_for('gestionar_alumnos'))
    
    # Crear nuevo alumno
    nuevo_alumno = UsuarioAlumno(
        username=username,
        nombre_completo=nombre_completo,
        grado_grupo=grado_grupo, # Guardamos "6A"
        password_hash=generate_password_hash(password),
        activo=True
    )
    
    db.session.add(nuevo_alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre_completo} inscrito en {grado_grupo}.', 'success')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/editar/<int:id>', methods=['POST'])
def editar_alumno(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    alumno = UsuarioAlumno.query.get_or_404(id)
    
    alumno.nombre_completo = request.form['nombre_completo']
    alumno.grado_grupo = request.form['grado_grupo']
    alumno.activo = 'activo' in request.form
    
    # Si se proporcionó una nueva contraseña, actualizarla
    nueva_password = request.form.get('password')
    if nueva_password:
        alumno.password_hash = generate_password_hash(nueva_password)
    
    db.session.commit()
    
    flash(f'Datos de {alumno.nombre_completo} actualizados.', 'success')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/eliminar/<int:id>')
def eliminar_alumno(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    alumno = UsuarioAlumno.query.get_or_404(id)
    nombre = alumno.nombre_completo
    
    db.session.delete(alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre} eliminado del sistema.', 'warning')
    return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/asistencia/tomar', methods=['POST'])
def tomar_asistencia():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Recibimos la fecha del formulario (o usamos hoy)
    fecha_str = request.form.get('fecha', datetime.utcnow().strftime('%Y-%m-%d'))
    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    
    # Recorremos el formulario. Los campos vienen como "asistencia_IDALUMNO"
    for key, value in request.form.items():
        if key.startswith('asistencia_'):
            alumno_id = int(key.split('_')[1])
            estado = value # P, F, R
            
            # Buscar si ya se tomó lista ese día para ese alumno (para actualizar en vez de duplicar)
            registro = Asistencia.query.filter_by(alumno_id=alumno_id, fecha=fecha_obj).first()
            
            if registro:
                registro.estado = estado # Actualizar
            else:
                # Crear nuevo
                nuevo = Asistencia(alumno_id=alumno_id, fecha=fecha_obj, estado=estado)
                db.session.add(nuevo)
    
    db.session.commit()
    flash(f'Asistencia del día {fecha_str} guardada correctamente.', 'success')
    return redirect(url_for('gestionar_alumnos', grado=request.form.get('grado_origen')))

@app.route('/admin/reporte-asistencia/<grupo>')
def generar_reporte_asistencia(grupo):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Obtener fechas del reporte
    fecha_inicio = request.args.get('fecha_inicio', date.today().isoformat())
    fecha_fin = request.args.get('fecha_fin', None)
    
    try:
        # 🔥 Generar el PDF (se guarda automáticamente en iDrive e2)
        url_guardado, buffer_pdf, nombre_archivo = generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin)
        
        # Mostrar mensaje al usuario
        if url_guardado:
            flash('✅ Reporte generado y guardado automáticamente en iDrive e2', 'success')
        else:
            flash('✅ Reporte generado correctamente', 'success')
        
        # 🔥 DESCARGAR el PDF directamente al navegador del usuario
        return send_file(
            buffer_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=nombre_archivo
        )
        
    except Exception as e:
        print(f"❌ Error completo: {str(e)}")
        flash(f'❌ Error al generar reporte: {str(e)}', 'danger')
        return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/descargar-reporte/<path:filename>')
def descargar_reporte(filename):
    """Ruta alternativa para descargar reportes guardados localmente"""
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    try:
        return send_from_directory(
            os.path.join(UPLOAD_FOLDER, 'reportes'),
            filename,
            as_attachment=True
        )
    except Exception as e:
        flash(f'Error al descargar reporte: {str(e)}', 'danger')
        return redirect(url_for('gestionar_alumnos'))

@app.route('/admin/alumnos/entregas')
def ver_entregas_alumnos():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Obtener todas las entregas ordenadas por fecha
    entregas = EntregaAlumno.query.order_by(EntregaAlumno.fecha_entrega.desc()).all()
    
    # Agrupar por alumno para estadísticas
    entregas_por_alumno = {}
    for entrega in entregas:
        if entrega.nombre_alumno not in entregas_por_alumno:
            entregas_por_alumno[entrega.nombre_alumno] = []
        entregas_por_alumno[entrega.nombre_alumno].append(entrega)
    
    return render_template('admin/entregas_alumnos.html', 
                         entregas=entregas,
                         entregas_por_alumno=entregas_por_alumno)

@app.route('/admin/alumnos/calificar/<int:id>', methods=['POST'])
def calificar_entrega(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    entrega = EntregaAlumno.query.get_or_404(id)
    entrega.estrellas = int(request.form['estrellas'])
    entrega.comentarios = request.form['comentarios']
    
    db.session.commit()
    
    flash(f'Entrega de {entrega.nombre_alumno} calificada con {entrega.estrellas} estrellas.', 'success')
    return redirect(url_for('ver_entregas_alumnos'))

# --- RUTAS PARA VER REPORTES GENERADOS ---

@app.route('/admin/reportes-asistencia')
def ver_reportes_asistencia():
    """Página para ver todos los reportes generados con filtros"""
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Obtener filtros
    filtro_grupo = request.args.get('grupo', 'Todos')
    filtro_mes = request.args.get('mes', '')
    filtro_anio = request.args.get('anio', '')
    
    # Query base
    query = ReporteAsistencia.query
    
    # Aplicar filtro de grupo
    if filtro_grupo and filtro_grupo != 'Todos':
        query = query.filter_by(grupo=filtro_grupo)
    
    # Aplicar filtro de mes/año
    if filtro_mes and filtro_anio:
        try:
            mes = int(filtro_mes)
            anio = int(filtro_anio)
            # Filtrar reportes del mes seleccionado
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
    
    # Ordenar por fecha de generación (más reciente primero)
    reportes = query.order_by(ReporteAsistencia.fecha_generacion.desc()).all()
    
    # Obtener lista de grupos únicos para el filtro
    grupos_disponibles = db.session.query(ReporteAsistencia.grupo).distinct().all()
    grupos_disponibles = [g[0] for g in grupos_disponibles]
    
    # Estadísticas
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
def descargar_reporte_guardado(reporte_id):
    """Descargar un reporte previamente generado"""
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        # Si está en S3
        if reporte.archivo_url and reporte.archivo_url.startswith('http'):
            if S3_ENDPOINT and S3_KEY and S3_SECRET:
                s3 = boto3.client('s3',
                                endpoint_url=S3_ENDPOINT,
                                aws_access_key_id=S3_KEY,
                                aws_secret_access_key=S3_SECRET,
                                region_name='us-west-1')
                
                # Extraer el key del archivo
                key = f"reportes/{reporte.nombre_archivo}"
                
                # Descargar desde S3
                s3_object = s3.get_object(Bucket=S3_BUCKET, Key=key)
                pdf_content = s3_object['Body'].read()
                
                return send_file(
                    BytesIO(pdf_content),
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=reporte.nombre_archivo
                )
        
        # Si está guardado localmente
        return send_from_directory(
            os.path.join(UPLOAD_FOLDER, 'reportes'),
            reporte.nombre_archivo,
            as_attachment=True
        )
        
    except Exception as e:
        flash(f'Error al descargar reporte: {str(e)}', 'danger')
        return redirect(url_for('ver_reportes_asistencia'))

@app.route('/admin/eliminar-reporte/<int:reporte_id>')
def eliminar_reporte(reporte_id):
    """Eliminar un reporte del registro (no borra el archivo físico)"""
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        # Opcionalmente eliminar de S3
        if reporte.archivo_url and reporte.archivo_url.startswith('http') and S3_ENDPOINT and S3_KEY and S3_SECRET:
            try:
                s3 = boto3.client('s3',
                                endpoint_url=S3_ENDPOINT,
                                aws_access_key_id=S3_KEY,
                                aws_secret_access_key=S3_SECRET,
                                region_name='us-west-1')
                key = f"reportes/{reporte.nombre_archivo}"
                s3.delete_object(Bucket=S3_BUCKET, Key=key)
                print(f"🗑️ Archivo eliminado de S3: {key}")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar de S3: {e}")
        
        # Eliminar registro de la base de datos
        db.session.delete(reporte)
        db.session.commit()
        flash('Reporte eliminado correctamente', 'success')
        
    except Exception as e:
        flash(f'Error al eliminar reporte: {str(e)}', 'danger')
    
    return redirect(url_for('ver_reportes_asistencia'))

# --- RUTAS DE ALUMNOS (PANEL Y SUBIDA DE TAREAS) ---

@app.route('/alumnos')
def panel_alumnos():
    if 'alumno_id' not in session or session.get('tipo_usuario') != 'alumno':
        return redirect(url_for('login_alumnos'))
    
    alumno = UsuarioAlumno.query.get(session['alumno_id'])
    
    # 1. Tareas entregadas
    mis_entregas = EntregaAlumno.query.filter_by(alumno_id=alumno.id).order_by(EntregaAlumno.fecha_entrega.desc()).all()
    
    # 2. LÓGICA SIMPLIFICADA (CAMBIO):
    # Ahora buscamos coincidencia EXACTA. Si soy "6A", busco exámenes para "6A".
    mi_grupo_exacto = session['alumno_grado'] # Ej: "6A"
    
    mis_cuestionarios = Cuestionario.query.filter_by(grado=mi_grupo_exacto).order_by(Cuestionario.fecha.desc()).all()
    
    # Resto de la función igual...
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
def subir_tarea():
    # SEGURIDAD: Verificar que esté logueado como alumno
    if 'alumno_id' not in session or session.get('tipo_usuario') != 'alumno':
        return redirect(url_for('login_alumnos'))
    
    if 'archivo' not in request.files:
        flash('No se subió archivo', 'danger')
        return redirect(url_for('panel_alumnos'))
    
    archivo = request.files['archivo']
    if archivo.filename == '':
        flash('Ningún archivo seleccionado', 'danger')
        return redirect(url_for('panel_alumnos'))

    if archivo:
        # Obtener datos del alumno
        alumno = UsuarioAlumno.query.get(session['alumno_id'])
        
        # Usamos nuestra función inteligente que decide si es S3 o Local
        ruta = guardar_archivo(archivo)
        
        # Guardar en DB
        nueva_entrega = EntregaAlumno(
            alumno_id=alumno.id,
            nombre_alumno=alumno.nombre_completo,
            grado_grupo=alumno.grado_grupo,
            archivo_url=ruta
        )
        db.session.add(nueva_entrega)
        db.session.commit()
        
        flash('¡Tarea enviada con éxito! El profesor la revisará pronto.', 'success')
        return redirect(url_for('panel_alumnos'))

# --- RUTAS DE INVENTARIO (CRUD) ---

@app.route('/admin/inventario')
def inventario():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
        
    # Obtener todos los equipos ordenados por ID
    equipos = Equipo.query.order_by(Equipo.id.desc()).all()
    return render_template('admin/inventario.html', equipos=equipos)

@app.route('/admin/inventario/agregar', methods=['POST'])
def agregar_equipo():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    nuevo_equipo = Equipo(
        tipo=request.form['tipo'],
        marca=request.form['marca'],
        modelo=request.form['modelo'],
        estado=request.form['estado'],
        qr_data=f"ME-{int(datetime.now().timestamp())}" # Generamos un ID único temporal para el QR
    )
    
    db.session.add(nuevo_equipo)
    db.session.commit()
    flash('Equipo agregado correctamente', 'success')
    return redirect(url_for('inventario'))

@app.route('/admin/inventario/eliminar/<int:id>')
def eliminar_equipo(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
        
    equipo = Equipo.query.get_or_404(id)
    db.session.delete(equipo)
    db.session.commit()
    flash('Equipo eliminado del inventario', 'warning')
    return redirect(url_for('inventario'))

@app.route('/admin/generar_qr/<int:id>')
def generar_qr(id):
    # VERIFICACIÓN DE SEGURIDAD
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Lógica simple para generar QR en memoria (o guardar imagen)
    equipo = Equipo.query.get_or_404(id)
    # Aquí luego implementaremos la generación real de la imagen QR
    return f"QR generado para {equipo.tipo} {equipo.marca}"

@app.route('/admin/generar_qr_img/<int:id>')
def generar_qr_img(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))

    equipo = Equipo.query.get_or_404(id)

    # Datos que llevará el QR (Simple y útil)
    info_qr = f"PROPIEDAD ESCUELA MARIANO ESCOBEDO\nID: {equipo.id}\nTipo: {equipo.tipo}\nMarca: {equipo.marca}\nModelo: {equipo.modelo}"

    # Crear el código QR en memoria
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(info_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Guardar en un buffer de memoria (bytes)
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)

    return send_file(img_io, mimetype='image/png')

# --- RUTAS DE MANTENIMIENTO ---

@app.route('/admin/mantenimiento')
def mantenimiento():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
        
    # Obtener reportes activos (sin reparar) y el historial (reparados)
    pendientes = Mantenimiento.query.filter_by(fecha_reparacion=None).all()
    historial = Mantenimiento.query.filter(Mantenimiento.fecha_reparacion != None).order_by(Mantenimiento.fecha_reparacion.desc()).limit(10).all()
    
    # Necesitamos la lista de equipos para el formulario de "Nuevo Reporte"
    equipos = Equipo.query.all()
    
    return render_template('admin/mantenimiento.html', pendientes=pendientes, historial=historial, equipos=equipos)

@app.route('/admin/mantenimiento/reportar', methods=['POST'])
def reportar_falla():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    equipo_id = request.form['equipo_id']
    descripcion = request.form['descripcion']
    
    # 1. Crear el reporte
    nuevo_reporte = Mantenimiento(equipo_id=equipo_id, descripcion_falla=descripcion)
    
    # 2. Actualizar el estado del equipo automáticamente a "En Reparación"
    equipo = Equipo.query.get(equipo_id)
    equipo.estado = "En Reparación"
    
    db.session.add(nuevo_reporte)
    db.session.commit()
    flash('Falla reportada. El equipo pasó a estado de reparación.', 'warning')
    return redirect(url_for('mantenimiento'))

@app.route('/admin/mantenimiento/solucionar', methods=['POST'])
def solucionar_falla():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
        
    reporte_id = request.form['reporte_id']
    solucion = request.form['solucion']
    
    # 1. Cerrar el reporte
    reporte = Mantenimiento.query.get(reporte_id)
    reporte.fecha_reparacion = datetime.utcnow()
    reporte.solucion = solucion
    
    # 2. Devolver el equipo a estado "Funcional" automáticamente
    reporte.equipo.estado = "Funcional"
    
    db.session.commit()
    flash('¡Equipo reparado exitosamente!', 'success')
    return redirect(url_for('mantenimiento'))

# --- RUTAS DE ANUNCIOS ---

@app.route('/admin/anuncios')
def gestionar_anuncios():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Ordenar por fecha, el más nuevo arriba
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).all()
    return render_template('admin/anuncios.html', anuncios=anuncios)

@app.route('/admin/anuncios/publicar', methods=['POST'])
def publicar_anuncio():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    titulo = request.form['titulo']
    contenido = request.form['contenido']
    
    nuevo_anuncio = Anuncio(titulo=titulo, contenido=contenido)
    db.session.add(nuevo_anuncio)
    db.session.commit()
    
    flash('¡Anuncio publicado en la página principal!', 'success')
    return redirect(url_for('gestionar_anuncios'))

@app.route('/admin/anuncios/eliminar/<int:id>')
def eliminar_anuncio(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
        
    anuncio = Anuncio.query.get_or_404(id)
    db.session.delete(anuncio)
    db.session.commit()
    flash('Anuncio eliminado.', 'secondary')
    return redirect(url_for('gestionar_anuncios'))

# --- RUTAS DE CUESTIONARIOS ---

@app.route('/admin/cuestionarios')
def gestionar_cuestionarios():
    if 'user' not in session:
        return redirect(url_for('login'))
    # Mostrar todos
    cuestionarios = Cuestionario.query.order_by(Cuestionario.fecha.desc()).all()
    return render_template('admin/cuestionarios.html', cuestionarios=cuestionarios)

@app.route('/admin/cuestionarios/publicar', methods=['POST'])
def publicar_cuestionario():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # NUEVA LÓGICA: El examen va para un grupo específico
    grado = request.form['grado'] # Ej: "6"
    grupo = request.form['grupo'] # Ej: "A"
    target = f"{grado}{grupo}"    # Resultado: "6A"
    
    nuevo = Cuestionario(
        titulo=request.form['titulo'],
        url=request.form['url'],
        grado=target # Guardamos "6A" en la base de datos
    )
    db.session.add(nuevo)
    db.session.commit()
    flash(f'Cuestionario asignado exclusivamente al grupo {target}.', 'success')
    return redirect(url_for('gestionar_cuestionarios'))

@app.route('/admin/cuestionarios/eliminar/<int:id>')
def eliminar_cuestionario(id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    item = Cuestionario.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Cuestionario eliminado.', 'secondary')
    return redirect(url_for('gestionar_cuestionarios'))

# --- RUTAS PÚBLICAS DE GRADOS ---

@app.route('/grado/<int:numero_grado>')
def ver_grado(numero_grado):
    # Buscar la info de este grado
    actividad = ActividadGrado.query.filter_by(grado=numero_grado).first()
    return render_template('publico/ver_grado.html', grado=numero_grado, actividad=actividad)

# --- RUTA ADMIN PARA EDITAR GRADOS ---

@app.route('/admin/grados', methods=['GET', 'POST'])
def gestionar_grados():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        grado_id = int(request.form['grado'])
        titulo = request.form['titulo']
        descripcion = request.form['descripcion']
        
        # Buscar si ya existe info para ese grado
        actividad = ActividadGrado.query.filter_by(grado=grado_id).first()
        
        if not actividad:
            # Si no existe, creamos uno nuevo
            actividad = ActividadGrado(grado=grado_id)
            db.session.add(actividad)
        
        # Actualizamos los datos
        actividad.titulo = titulo
        actividad.descripcion = descripcion
        actividad.fecha_actualizacion = datetime.utcnow()
        
        db.session.commit()
        flash(f'¡Información de {grado_id}° actualizada!', 'success')
        return redirect(url_for('gestionar_grados'))

    # Para mostrar la página de edición, cargamos lo que ya existe
    actividades = ActividadGrado.query.all()
    # Lo convertimos a un diccionario para fácil acceso en el HTML: {1: actividad_1, 2: actividad_2...}
    info_grados = {a.grado: a for a in actividades}
    
    return render_template('admin/gestionar_grados.html', info_grados=info_grados)

# --- GESTIÓN DE HORARIOS ---

@app.route('/admin/horarios')
def gestionar_horarios():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    horarios = Horario.query.all()
    return render_template('admin/horarios.html', horarios=horarios)

@app.route('/admin/horarios/agregar', methods=['POST'])
def agregar_horario():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
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
def eliminar_horario(id):
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    horario = Horario.query.get_or_404(id)
    db.session.delete(horario)
    db.session.commit()
    flash('Horario eliminado.', 'warning')
    return redirect(url_for('gestionar_horarios'))

# --- GESTIÓN DE PLATAFORMAS ---

@app.route('/admin/plataformas')
def gestionar_plataformas():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    plataformas = Plataforma.query.all()
    return render_template('admin/plataformas.html', plataformas=plataformas)

@app.route('/admin/plataformas/agregar', methods=['POST'])
def agregar_plataforma():
    if 'user' not in session:
        return redirect(url_for('login'))
    
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
def eliminar_plataforma(id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    p = Plataforma.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash('Plataforma eliminada.', 'warning')
    return redirect(url_for('gestionar_plataformas'))

# --- GESTIÓN DE ENTREGAS (CON FILTRO) ---

@app.route('/admin/entregas')
def gestionar_entregas():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # 1. Capturamos el filtro de la URL (Ej: ?grado=6A)
    filtro = request.args.get('grado')
    
    # 2. Iniciamos la consulta uniendo Entregas con Alumnos
    # (Necesitamos 'join' para saber el grado del alumno que mandó la tarea)
    query = EntregaAlumno.query.join(UsuarioAlumno)
    
    # 3. Aplicamos el filtro si existe
    if filtro and filtro != 'Todos':
        query = query.filter(UsuarioAlumno.grado_grupo == filtro)
    
    # 4. Ordenamos: Primero las más recientes
    entregas = query.order_by(EntregaAlumno.fecha_entrega.desc()).all()
    
    return render_template('admin/entregas.html', 
                         entregas=entregas, 
                         filtro_actual=filtro)

# --- RUTA PARA SERVIR ARCHIVOS LOCALES (IMPORTANTE) ---
# Esta ruta permite ver las imágenes si están guardadas en tu PC
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- INICIALIZADOR ---

# ESTO ES LO NUEVO: Lo sacamos del "if" y lo ponemos solito.
# Así Gunicorn lo leerá y creará las tablas en Neon al arrancar.
with app.app_context():
    db.create_all()

# Esto se queda solo para cuando pruebas en tu PC
if __name__ == '__main__':
    app.run(debug=True, port=5000)