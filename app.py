import os
import boto3
import qrcode
import io  # Para manejar archivos en memoria
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# --- CONFIGURACIÓN INICIAL ---
app = Flask(__name__)
app.secret_key = 'clave_secreta_desarrollo'  # Cambiar en producción

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
# CAMBIO: Ahora sí lee la variable, y si no existe usa el de ejemplo por seguridad
S3_BUCKET = os.environ.get('S3_BUCKET', 'taller-computo-files')

# Carpeta local de respaldo si no hay S3
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Crea la carpeta si no existe

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

# --- FUNCIONES AUXILIARES (HELPER FUNCTIONS) ---

def guardar_archivo(archivo):
    """
    Guarda archivo en S3 si hay credenciales, sino en carpeta local 'uploads'.
    Retorna: El nombre del archivo o URL para guardar en DB.
    """
    filename = secure_filename(archivo.filename)
    
    # Intento de S3
    if S3_ENDPOINT and S3_KEY and S3_SECRET:
        try:
            s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT,
                              aws_access_key_id=S3_KEY,
                              aws_secret_access_key=S3_SECRET)
            s3.upload_fileobj(archivo, S3_BUCKET, filename)
            return f"{S3_ENDPOINT}/{S3_BUCKET}/{filename}"
        except Exception as e:
            print(f"Error S3 (usando local): {e}")
    
    # Fallback Local
    archivo.save(os.path.join(UPLOAD_FOLDER, filename))
    return filename

# --- RUTAS PRINCIPALES ---

@app.route('/')
def index():
    # 1. Obtener Anuncios
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(5).all()
    
    # 2. Obtener Horarios (Ordenados por día es difícil con texto, los mostraremos como se creen)
    # Opcional: Podríamos ordenarlos por ID para que salgan en el orden que los agregaste
    horarios = Horario.query.all()
    
    # 3. Obtener Grados para el menú (si lo usas)
    # ... tu lógica existente ...

    return render_template('index.html', anuncios=anuncios, horarios=horarios)

# --- RUTAS DE AUTENTICACIÓN (LOGIN PROFESOR) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # USUARIO Y CONTRASEÑA "QUEMADOS" PARA USO LOCAL (SIMPLE)
        # Puedes cambiar 'admin' y 'profesor123' por lo que quieras
        if username == 'admin' and password == 'profesor123':
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
    
    return render_template('admin/dashboard.html', 
                         total_equipos=equipos, 
                         reparaciones=pendientes,
                         alumnos_activos=alumnos_activos,
                         total_entregas=total_entregas)

# --- RUTAS DE GESTIÓN DE ALUMNOS (DESDE EL PROFESOR) ---

@app.route('/admin/alumnos')
def gestionar_alumnos():
    if 'user' not in session or session.get('tipo_usuario') != 'profesor':
        return redirect(url_for('login'))
    
    # Obtener todos los alumnos ordenados por grado y nombre
    alumnos = UsuarioAlumno.query.order_by(UsuarioAlumno.grado_grupo, UsuarioAlumno.nombre_completo).all()
    
    # Obtener estadísticas
    total_alumnos = UsuarioAlumno.query.count()
    alumnos_activos = UsuarioAlumno.query.filter_by(activo=True).count()
    
    return render_template('admin/alumnos.html', 
                         alumnos=alumnos, 
                         total_alumnos=total_alumnos,
                         alumnos_activos=alumnos_activos)

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