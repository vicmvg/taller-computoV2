# web/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from web.models import UsuarioAlumno, Configuracion
from web.extensions import db
from web.utils import get_current_user

auth_bp = Blueprint('auth', __name__)

# --- LOGIN PARA PROFESORES ---
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    user_info = get_current_user()
    if user_info and user_info[0] == 'profesor':
        return redirect(url_for('admin.dashboard'))

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
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Contraseña incorrecta.', 'danger')
        else:
            flash('Usuario incorrecto.', 'danger')
            
    return render_template('login.html')  # ✅ Sin prefijo porque está en la raíz de templates/

# --- LOGIN PARA ALUMNOS ---
@auth_bp.route('/login-alumnos', methods=['GET', 'POST'])
def login_alumnos():
    user_info = get_current_user()
    if user_info and user_info[0] == 'alumno':
        return redirect(url_for('alumno.dashboard'))

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
            return redirect(url_for('alumno.dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
            return redirect(url_for('auth.login_alumnos'))
        
    return render_template('alumnos/login.html')

# --- LOGOUT ---
@auth_bp.route('/logout')
def logout():
    tipo_usuario = session.get('tipo_usuario')
    session.clear()
    
    if tipo_usuario == 'profesor':
        flash('Sesión cerrada correctamente.', 'success')
        return redirect(url_for('auth.login'))
    else:
        flash('Sesión cerrada correctamente.', 'success')
        return redirect(url_for('auth.login_alumnos'))

# --- RECUPERAR ACCESO (PROFESOR) ---
@auth_bp.route('/recuperar-acceso', methods=['GET', 'POST'])
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
            return redirect(url_for('auth.login'))
        else:
            flash('Token maestro incorrecto o usuario no válido.', 'danger')
            
    return render_template('recuperar.html')  # ✅ Sin prefijo porque está en la raíz de templates/

# --- LOGOUT ALUMNOS (RUTA SEPARADA PARA COMPATIBILIDAD) ---
@auth_bp.route('/logout-alumnos')
def logout_alumnos():
    session.clear()
    flash('Sesión cerrada correctamente.', 'success')
    return redirect(url_for('index'))  # ✅ CORREGIDO: Cambiado de 'public.index' a 'index'