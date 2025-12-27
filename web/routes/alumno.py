# web/routes/alumno.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, send_file, send_from_directory
from web.models import UsuarioAlumno, EntregaAlumno, Cuestionario, Anuncio, Asistencia, Pago, ReciboPago, SolicitudArchivo, ArchivoEnviado, Mensaje, MensajeFlotante, MensajeLeido
from web.extensions import db
from web.utils import require_alumno, s3_manager, file_validator, guardar_archivo, chat_limiter, log_error
from datetime import datetime
import os

# Definimos el Blueprint
alumno_bp = Blueprint('alumno', __name__, url_prefix='/alumnos')

@alumno_bp.route('/')
@require_alumno
def dashboard():
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    # Lógica del panel de alumnos
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

@alumno_bp.route('/entregas')
@require_alumno
def mis_entregas():
    alumno_id = session.get('alumno_id')
    entregas = EntregaAlumno.query.filter_by(alumno_id=alumno_id).all()
    return render_template('alumno/entregas.html', entregas=entregas)

@alumno_bp.route('/subir-tarea', methods=['POST'])
@require_alumno
def subir_tarea():
    if 'archivo' not in request.files:
        flash('No se subió archivo', 'danger')
        return redirect(url_for('alumno.dashboard'))
    
    archivo = request.files['archivo']
    titulo_tarea = request.form.get('titulo_tarea', '').strip()
    
    if archivo.filename == '':
        flash('Ningún archivo seleccionado', 'danger')
        return redirect(url_for('alumno.dashboard'))
    
    if not titulo_tarea:
        flash('⚠️ Debes escribir el nombre de la tarea', 'warning')
        return redirect(url_for('alumno.dashboard'))

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
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.dashboard'))

# --- RUTA DE ASISTENCIA ---
@alumno_bp.route('/asistencia')
@require_alumno
def mi_asistencia():
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    asistencias = Asistencia.query.filter_by(alumno_id=alumno_id).order_by(Asistencia.fecha.desc()).all()
    return render_template('alumnos/asistencia.html', asistencias=asistencias, alumno=alumno)

# --- RUTAS DE PAGOS Y RECIBOS ---
@alumno_bp.route('/pagos')
@require_alumno
def mis_pagos():
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    pagos = Pago.query.filter_by(alumno_id=alumno_id).all()
    return render_template('alumnos/pagos.html', pagos=pagos, alumno=alumno)

@alumno_bp.route('/recibos')
@require_alumno
def mis_recibos():
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    pagos = Pago.query.filter_by(alumno_id=alumno_id).all()
    recibos = []
    for pago in pagos:
        recibos.extend(pago.recibos)
    return render_template('alumnos/recibos.html', recibos=recibos, alumno=alumno)

@alumno_bp.route('/descargar-recibo/<int:recibo_id>')
@require_alumno
def descargar_recibo_alumno(recibo_id):
    recibo = ReciboPago.query.get_or_404(recibo_id)
    
    if recibo.pago.alumno_id != session.get('alumno_id'):
        flash('No tienes permiso para descargar este recibo', 'danger')
        return redirect(url_for('alumno.mis_recibos'))
    
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
                os.path.join('uploads', 'pagos', 'recibos'),
                recibo.nombre_archivo,
                as_attachment=True
            )
    except Exception as e:
        log_error(f"Error al descargar recibo: {str(e)}")
        flash(f'Error al descargar recibo: {str(e)}', 'danger')
        return redirect(url_for('alumno.mis_recibos'))

# --- RUTA DE CUESTIONARIOS ---
@alumno_bp.route('/cuestionarios')
@require_alumno
def mis_cuestionarios():
    alumno = UsuarioAlumno.query.get(session['alumno_id'])
    mi_grupo_exacto = session['alumno_grado']
    cuestionarios = Cuestionario.query.filter_by(grado=mi_grupo_exacto).order_by(Cuestionario.fecha.desc()).all()
    return render_template('alumnos/cuestionarios.html', cuestionarios=cuestionarios, alumno=alumno)

# --- RUTA DE PERFIL ---
@alumno_bp.route('/perfil/foto', methods=['POST'])
@require_alumno
def actualizar_foto_perfil():
    if 'foto' not in request.files:
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('alumno.dashboard'))
    
    foto = request.files['foto']
    
    if foto.filename == '':
        flash('No se seleccionó ninguna foto', 'danger')
        return redirect(url_for('alumno.dashboard'))
    
    ext = foto.filename.rsplit('.', 1)[1].lower() if '.' in foto.filename else ''
    if ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']:
        flash('⚠️ Solo se permiten archivos de imagen (PNG, JPG, GIF, WEBP)', 'danger')
        return redirect(url_for('alumno.dashboard'))
    
    try:
        ruta_foto, es_s3 = guardar_archivo(foto)
        alumno = UsuarioAlumno.query.get(session['alumno_id'])
        alumno.foto_perfil = ruta_foto
        db.session.commit()
        
        flash('¡Foto de perfil actualizada correctamente! 🎉', 'success')
        
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.dashboard'))

# --- RUTAS DE CHAT ---
@alumno_bp.route('/chat/enviar', methods=['POST'])
@require_alumno
def enviar_mensaje():
    alumno_key = f"alumno_{session['alumno_id']}"
    if not chat_limiter.is_allowed(alumno_key):
        return jsonify({'status': 'error', 'msg': 'Demasiados mensajes. Espera un momento.'}), 429
    
    contenido = request.form.get('mensaje')
    if not contenido or contenido.strip() == '':
        return jsonify({'status': 'error', 'msg': 'Mensaje vacío'}), 400

    nuevo = Mensaje(
        alumno_id=session['alumno_id'],
        nombre_alumno=session['alumno_nombre'],
        grado_grupo=session['alumno_grado'],
        contenido=contenido
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    return jsonify({'status': 'ok'})

@alumno_bp.route('/chat/obtener')
@require_alumno
def obtener_mensajes():
    mi_grupo = session['alumno_grado']
    mensajes = Mensaje.query.filter_by(grado_grupo=mi_grupo).order_by(Mensaje.fecha.asc()).all()
    
    lista_mensajes = []
    for m in mensajes:
        es_mio = (m.alumno_id == session['alumno_id'])
        lista_mensajes.append({
            'nombre': 'Yo' if es_mio else m.nombre_alumno,
            'texto': m.contenido,
            'es_mio': es_mio,
            'hora': m.fecha.strftime('%H:%M')
        })

    return jsonify({
        'mensajes': lista_mensajes,
        'activo': True
    })

# --- RUTAS DE SISTEMA DE ARCHIVOS ---
@alumno_bp.route('/solicitar-archivo', methods=['GET', 'POST'])
@require_alumno
def solicitar_archivo():
    alumno_id = session.get('alumno_id')
    
    if request.method == 'POST':
        tipo_documento = request.form.get('tipo_documento')
        mensaje = request.form.get('mensaje')
        
        if not tipo_documento or not mensaje:
            flash('Debe completar todos los campos', 'danger')
            return redirect(url_for('alumno.solicitar_archivo'))
        
        nueva_solicitud = SolicitudArchivo(
            alumno_id=alumno_id,
            tipo_documento=tipo_documento,
            mensaje=mensaje,
            estado='pendiente'
        )
        
        db.session.add(nueva_solicitud)
        db.session.commit()
        
        flash('✅ Solicitud enviada correctamente. El profesor la revisará pronto.', 'success')
        return redirect(url_for('alumno.dashboard'))
    
    alumno = UsuarioAlumno.query.get(alumno_id)
    solicitudes = SolicitudArchivo.query.filter_by(alumno_id=alumno_id).order_by(SolicitudArchivo.fecha_solicitud.desc()).all()
    
    pendientes = SolicitudArchivo.query.filter_by(alumno_id=alumno_id, estado='pendiente').count()
    
    return render_template('alumnos/solicitar_archivo.html', 
                         alumno=alumno,
                         solicitudes=solicitudes,
                         pendientes=pendientes)

@alumno_bp.route('/mis-archivos')
@require_alumno
def ver_mis_archivos():
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    archivos = ArchivoEnviado.query.filter_by(alumno_id=alumno_id).order_by(ArchivoEnviado.fecha_envio.desc()).all()
    
    no_leidos = ArchivoEnviado.query.filter_by(alumno_id=alumno_id, leido=False).count()
    
    return render_template('alumnos/mis_archivos.html',
                         alumno=alumno,
                         archivos=archivos,
                         no_leidos=no_leidos)

@alumno_bp.route('/archivo/<int:archivo_id>/descargar')
@require_alumno
def descargar_archivo_alumno(archivo_id):
    alumno_id = session.get('alumno_id')
    archivo = ArchivoEnviado.query.get_or_404(archivo_id)
    
    if archivo.alumno_id != alumno_id:
        flash('No tienes permiso para descargar este archivo', 'danger')
        return redirect(url_for('alumno.ver_mis_archivos'))
    
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
                os.path.join('uploads', 'archivos_enviados'),
                archivo.nombre_archivo,
                as_attachment=True
            )
    except Exception as e:
        log_error(f"Error al descargar archivo: {str(e)}")
        flash(f'Error al descargar archivo: {str(e)}', 'danger')
        return redirect(url_for('alumno.ver_mis_archivos'))

# --- RUTAS DE MENSAJES FLOTANTES ---
@alumno_bp.route('/mensajes-flotantes/obtener')
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

@alumno_bp.route('/mensajes-flotantes/marcar-leido/<int:mensaje_id>', methods=['POST'])
@require_alumno
def marcar_mensaje_leido(mensaje_id):
    alumno_id = session.get('alumno_id')
    
    existe = MensajeLeido.query.filter_by(mensaje_id=mensaje_id, alumno_id=alumno_id).first()
    
    if not existe:
        nuevo_leido = MensajeLeido(mensaje_id=mensaje_id, alumno_id=alumno_id)
        
        db.session.add(nuevo_leido)
        db.session.commit()
    
    return jsonify({'status': 'ok'})

# --- API PARA NOTIFICACIONES ---
@alumno_bp.route('/archivos-nuevos/cantidad')
@require_alumno
def cantidad_archivos_nuevos():
    alumno_id = session.get('alumno_id')
    cantidad = ArchivoEnviado.query.filter_by(alumno_id=alumno_id, leido=False).count()
    return jsonify({'cantidad': cantidad})

# --- RUTA PARA VER ARCHIVOS (fotos de perfil, entregas, etc) ---
@alumno_bp.route('/ver-archivo/<path:archivo_path>')
@require_alumno
def ver_archivo(archivo_path):
    """Permite ver/descargar archivos de entregas y fotos de perfil"""
    try:
        # Si es una ruta de S3 completa
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
                return redirect(url_for('alumno.dashboard'))
        else:
            # Archivo local
            filename = archivo_path
            file_path = os.path.join('uploads', filename)
            
            if os.path.exists(file_path):
                # Determinar mimetype
                if filename.endswith('.pdf'):
                    mimetype = 'application/pdf'
                elif filename.endswith(('.doc', '.docx')):
                    mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif filename.endswith(('.jpg', '.jpeg')):
                    mimetype = 'image/jpeg'
                elif filename.endswith('.png'):
                    mimetype = 'image/png'
                elif filename.endswith('.gif'):
                    mimetype = 'image/gif'
                elif filename.endswith('.webp'):
                    mimetype = 'image/webp'
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
                return redirect(url_for('alumno.dashboard'))
                
    except Exception as e:
        log_error(f"Error al servir archivo: {str(e)}")
        flash(f'Error al cargar el archivo: {str(e)}', 'danger')
        return redirect(url_for('alumno.dashboard'))

@alumno_bp.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente.')
    return redirect(url_for('index'))  # ← CORREGIDO: 'index' en lugar de 'public.index'