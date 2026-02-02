# web/routes/alumno.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, send_file, send_from_directory
from web.models import (UsuarioAlumno, EntregaAlumno, Cuestionario, Anuncio, 
                        Asistencia, Pago, ReciboPago, SolicitudArchivo, 
                        ArchivoEnviado, Mensaje, MensajeFlotante, MensajeLeido, 
                        Configuracion, Encuesta, RespuestaEncuesta, ApunteClase,
                        EspacioColaborativo, MiembroEspacio, RolAsignado,
                        ArchivoColaborativo, IdeaColaborativa)  # ← AGREGAR ESTO
from web.extensions import db
from web.utils import require_alumno, s3_manager, file_validator, guardar_archivo, chat_limiter, log_error, chat_moderator
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
    """Ver todas las entregas del alumno"""
    alumno_id = session.get('alumno_id')
    entregas = EntregaAlumno.query.filter_by(alumno_id=alumno_id).order_by(EntregaAlumno.fecha_entrega.desc()).all()
    alumno = UsuarioAlumno.query.get(alumno_id)
    return render_template('alumnos/entregas.html', entregas=entregas, alumno=alumno)

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
@alumno_bp.route('/api/chat/enviar', methods=['POST'])
@require_alumno
def enviar_mensaje():
    alumno_id = session['alumno_id']
    alumno_key = f"alumno_{alumno_id}"
    
    # 1. Verificar rate limiting
    if not chat_limiter.is_allowed(alumno_key):
        return jsonify({
            'status': 'error', 
            'msg': '⏳ Demasiados mensajes. Espera un momento.'
        }), 429
    
    contenido = request.form.get('mensaje', '').strip()
    
    if not contenido:
        return jsonify({'status': 'error', 'msg': 'Mensaje vacío'}), 400
    
    # 2. ✅ MODERAR EL MENSAJE
    resultado_moderacion = chat_moderator.procesar_mensaje(alumno_id, contenido)
    
    if not resultado_moderacion['permitido']:
        # Mensaje bloqueado
        return jsonify({
            'status': 'bloqueado',
            'tipo': resultado_moderacion['tipo_accion'],
            'msg': resultado_moderacion['mensaje_sistema']
        }), 403
    
    # 3. Mensaje aprobado, guardar en BD
    nuevo = Mensaje(
        alumno_id=alumno_id,
        nombre_alumno=session['alumno_nombre'],
        grado_grupo=session['alumno_grado'],
        contenido=contenido
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    return jsonify({'status': 'ok'})

@alumno_bp.route('/api/chat/obtener')
@require_alumno
def obtener_mensajes():
    mi_grupo = session['alumno_grado']
    mensajes = Mensaje.query.filter_by(grado_grupo=mi_grupo).order_by(Mensaje.fecha.asc()).all()
    
    # ✅ AGREGAR VERIFICACIÓN DEL ESTADO DEL CHAT
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

    return jsonify({
        'mensajes': lista_mensajes,
        'activo': chat_activo  # ✅ IMPORTANTE: Devolver el estado
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
@alumno_bp.route('/api/mensajes-flotantes/obtener')  # ✅ CON /api/
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

@alumno_bp.route('/api/mensajes-flotantes/marcar-leido/<int:mensaje_id>', methods=['POST'])  # ✅ CON /api/
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
@alumno_bp.route('/api/archivos-nuevos/cantidad')  # ✅ CON /api/
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

# --- RUTAS PARA SISTEMA DE ENCUESTAS - ALUMNO ---
# =============================================================================
# ENCUESTAS - RESPUESTAS DE ALUMNOS
# =============================================================================

@alumno_bp.route('/encuesta/pendiente')
@require_alumno
def verificar_encuesta_pendiente():
    """API para verificar si hay encuesta pendiente (se llama desde JS)"""
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    if not alumno:
        return jsonify({'tiene_pendiente': False})
    
    # Buscar encuestas activas, obligatorias que aplican para este grupo
    encuestas_activas = Encuesta.query.filter_by(activa=True, obligatoria=True).all()
    
    for encuesta in encuestas_activas:
        # Verificar si aplica para el grupo del alumno
        if encuesta.aplica_para_grupo(alumno.grado_grupo):
            # Verificar si ya respondió
            if not encuesta.alumno_ya_respondio(alumno_id):
                # Tiene una encuesta pendiente
                return jsonify({
                    'tiene_pendiente': True,
                    'encuesta_id': encuesta.id,
                    'titulo': encuesta.titulo,
                    'descripcion': encuesta.descripcion
                })
    
    return jsonify({'tiene_pendiente': False})


@alumno_bp.route('/encuesta/<int:encuesta_id>/responder', methods=['POST'])
@require_alumno
def responder_encuesta(encuesta_id):
    """Guardar respuesta de encuesta del alumno"""
    try:
        alumno_id = session.get('alumno_id')
        alumno = UsuarioAlumno.query.get(alumno_id)
        encuesta = Encuesta.query.get_or_404(encuesta_id)
        
        if not alumno:
            return jsonify({'success': False, 'error': 'Alumno no encontrado'}), 400
        
        # Verificar que no haya respondido ya
        if encuesta.alumno_ya_respondio(alumno_id):
            return jsonify({'success': False, 'error': 'Ya respondiste esta encuesta'}), 400
        
        # Obtener respuestas del formulario
        pregunta1 = int(request.form.get('pregunta1', 0))
        pregunta2 = int(request.form.get('pregunta2', 0))
        pregunta3 = int(request.form.get('pregunta3', 0))
        pregunta4 = int(request.form.get('pregunta4', 0))
        pregunta5 = int(request.form.get('pregunta5', 0))
        
        comentario_positivo = request.form.get('comentario_positivo', '').strip()
        comentario_mejora = request.form.get('comentario_mejora', '').strip()
        comentario_adicional = request.form.get('comentario_adicional', '').strip()
        
        # Validar que todas las preguntas obligatorias estén respondidas
        if any(p < 1 or p > 5 for p in [pregunta1, pregunta2, pregunta3, pregunta4, pregunta5]):
            return jsonify({'success': False, 'error': 'Debes responder todas las preguntas'}), 400
        
        # Crear respuesta
        respuesta = RespuestaEncuesta(
            encuesta_id=encuesta_id,
            alumno_id=alumno_id,
            nombre_alumno=alumno.nombre_completo,
            grado_grupo=alumno.grado_grupo,
            pregunta1_clases=pregunta1,
            pregunta2_aprendizaje=pregunta2,
            pregunta3_maestro=pregunta3,
            pregunta4_contenido=pregunta4,
            pregunta5_dificultad=pregunta5,
            comentario_positivo=comentario_positivo if comentario_positivo else None,
            comentario_mejora=comentario_mejora if comentario_mejora else None,
            comentario_adicional=comentario_adicional if comentario_adicional else None
        )
        
        db.session.add(respuesta)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '¡Gracias por tu retroalimentación! 😊'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================================================
# APUNTES DE CLASE - RUTAS
# =============================================================================

@alumno_bp.route('/apuntes')
@require_alumno
def mis_apuntes():
    """Ver todos los apuntes del alumno"""
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    # Obtener todos los apuntes ordenados por fecha más reciente
    apuntes = ApunteClase.query.filter_by(
        alumno_id=alumno_id
    ).order_by(ApunteClase.fecha_clase.desc()).all()
    
    return render_template('alumnos/apuntes.html', apuntes=apuntes, alumno=alumno)


@alumno_bp.route('/apuntes/nuevo', methods=['GET', 'POST'])
@require_alumno
def nuevo_apunte():
    """Crear un nuevo apunte"""
    if request.method == 'POST':
        try:
            alumno_id = session.get('alumno_id')
            
            # Obtener datos del formulario
            fecha_clase = request.form.get('fecha_clase')
            materia = request.form.get('materia', 'Computación')
            tema = request.form.get('tema', '').strip()
            
            # Validar que al menos tenga tema
            if not tema:
                flash('⚠️ El tema de la clase es obligatorio', 'warning')
                return redirect(url_for('alumno.nuevo_apunte'))
            
            # Crear apunte
            nuevo_apunte = ApunteClase(
                alumno_id=alumno_id,
                fecha_clase=datetime.strptime(fecha_clase, '%Y-%m-%d').date() if fecha_clase else datetime.now().date(),
                materia=materia,
                tema=tema,
                de_que_trato=request.form.get('de_que_trato', '').strip() or None,
                conceptos_principales=request.form.get('conceptos_principales', '').strip() or None,
                lo_que_aprendi=request.form.get('lo_que_aprendi', '').strip() or None,
                mis_dudas=request.form.get('mis_dudas', '').strip() or None,
                lo_mejor=request.form.get('lo_mejor', '').strip() or None,
                tareas_seguimiento=request.form.get('tareas_seguimiento', '').strip() or None,
                notas_adicionales=request.form.get('notas_adicionales', '').strip() or None
            )
            
            db.session.add(nuevo_apunte)
            db.session.commit()
            
            flash('📝 ¡Apunte guardado exitosamente!', 'success')
            return redirect(url_for('alumno.mis_apuntes'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar el apunte: {str(e)}', 'danger')
            return redirect(url_for('alumno.nuevo_apunte'))
    
    # GET - Mostrar formulario
    alumno = UsuarioAlumno.query.get(session.get('alumno_id'))
    return render_template('alumnos/nuevo_apunte.html', alumno=alumno, fecha_hoy=datetime.now().strftime('%Y-%m-%d'))


@alumno_bp.route('/apuntes/<int:apunte_id>')
@require_alumno
def ver_apunte(apunte_id):
    """Ver un apunte específico"""
    alumno_id = session.get('alumno_id')
    apunte = ApunteClase.query.get_or_404(apunte_id)
    
    # Verificar que el apunte pertenece al alumno
    if apunte.alumno_id != alumno_id:
        flash('⚠️ No tienes permiso para ver este apunte', 'danger')
        return redirect(url_for('alumno.mis_apuntes'))
    
    alumno = UsuarioAlumno.query.get(alumno_id)
    return render_template('alumnos/ver_apunte.html', apunte=apunte, alumno=alumno)


@alumno_bp.route('/apuntes/<int:apunte_id>/editar', methods=['GET', 'POST'])
@require_alumno
def editar_apunte(apunte_id):
    """Editar un apunte existente"""
    alumno_id = session.get('alumno_id')
    apunte = ApunteClase.query.get_or_404(apunte_id)
    
    # Verificar que el apunte pertenece al alumno
    if apunte.alumno_id != alumno_id:
        flash('⚠️ No tienes permiso para editar este apunte', 'danger')
        return redirect(url_for('alumno.mis_apuntes'))
    
    if request.method == 'POST':
        try:
            # Actualizar campos
            fecha_clase = request.form.get('fecha_clase')
            tema = request.form.get('tema', '').strip()
            
            if not tema:
                flash('⚠️ El tema de la clase es obligatorio', 'warning')
                return redirect(url_for('alumno.editar_apunte', apunte_id=apunte_id))
            
            apunte.fecha_clase = datetime.strptime(fecha_clase, '%Y-%m-%d').date() if fecha_clase else apunte.fecha_clase
            apunte.materia = request.form.get('materia', 'Computación')
            apunte.tema = tema
            apunte.de_que_trato = request.form.get('de_que_trato', '').strip() or None
            apunte.conceptos_principales = request.form.get('conceptos_principales', '').strip() or None
            apunte.lo_que_aprendi = request.form.get('lo_que_aprendi', '').strip() or None
            apunte.mis_dudas = request.form.get('mis_dudas', '').strip() or None
            apunte.lo_mejor = request.form.get('lo_mejor', '').strip() or None
            apunte.tareas_seguimiento = request.form.get('tareas_seguimiento', '').strip() or None
            apunte.notas_adicionales = request.form.get('notas_adicionales', '').strip() or None
            apunte.fecha_modificacion = datetime.utcnow()
            
            db.session.commit()
            
            flash('✅ ¡Apunte actualizado exitosamente!', 'success')
            return redirect(url_for('alumno.ver_apunte', apunte_id=apunte_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el apunte: {str(e)}', 'danger')
            return redirect(url_for('alumno.editar_apunte', apunte_id=apunte_id))
    
    alumno = UsuarioAlumno.query.get(alumno_id)
    return render_template('alumnos/editar_apunte.html', apunte=apunte, alumno=alumno)


@alumno_bp.route('/apuntes/<int:apunte_id>/eliminar', methods=['POST'])
@require_alumno
def eliminar_apunte(apunte_id):
    """Eliminar un apunte"""
    alumno_id = session.get('alumno_id')
    apunte = ApunteClase.query.get_or_404(apunte_id)
    
    # Verificar que el apunte pertenece al alumno
    if apunte.alumno_id != alumno_id:
        flash('⚠️ No tienes permiso para eliminar este apunte', 'danger')
        return redirect(url_for('alumno.mis_apuntes'))
    
    try:
        db.session.delete(apunte)
        db.session.commit()
        flash('🗑️ Apunte eliminado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.mis_apuntes'))

# ============================================
# RUTAS PARA ESPACIOS COLABORATIVOS (ALUMNOS)
# ============================================

@alumno_bp.route('/espacios-colaborativos')
@require_alumno
def mis_espacios_colaborativos():
    """Ver todos los espacios colaborativos del alumno"""
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    # ✅ CORRECCIÓN: El filtro activo está en EspacioColaborativo
    mis_espacios = db.session.query(EspacioColaborativo).join(
        MiembroEspacio
    ).filter(
        MiembroEspacio.alumno_id == alumno_id,
        EspacioColaborativo.activo == True  # ← CORREGIDO
    ).order_by(EspacioColaborativo.fecha_creacion.desc()).all()
    
    return render_template('alumnos/espacios_colaborativos.html',
                         alumno=alumno,
                         mis_espacios=mis_espacios)


@alumno_bp.route('/espacios-colaborativos/<int:espacio_id>')
@require_alumno
def ver_espacio_colaborativo(espacio_id):
    """Ver un espacio colaborativo específico"""
    alumno_id = session.get('alumno_id')
    alumno = UsuarioAlumno.query.get(alumno_id)
    
    # Verificar que el alumno es miembro activo del espacio
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        flash('⚠️ No tienes acceso a este espacio colaborativo', 'danger')
        return redirect(url_for('alumno.mis_espacios_colaborativos'))
    
    # Obtener el espacio
    espacio = EspacioColaborativo.query.get_or_404(espacio_id)
    
    # Obtener miembros activos
    miembros = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        activo=True
    ).join(UsuarioAlumno).order_by(UsuarioAlumno.nombre_completo).all()
    
    # Obtener archivos del espacio
    archivos = ArchivoColaborativo.query.filter_by(
        espacio_id=espacio_id
    ).order_by(ArchivoColaborativo.fecha_subida.desc()).all()
    
    # Obtener ideas del espacio
    ideas = IdeaColaborativa.query.filter_by(
        espacio_id=espacio_id
    ).order_by(IdeaColaborativa.fecha_creacion.desc()).all()
    
    return render_template('alumnos/ver_espacio_colaborativo.html',
                         alumno=alumno,
                         espacio=espacio,
                         miembros=miembros,
                         archivos=archivos,
                         ideas=ideas,
                         miembro=miembro)


@alumno_bp.route('/espacios-colaborativos/<int:espacio_id>/subir-archivo', methods=['POST'])
@require_alumno
def subir_archivo_colaborativo(espacio_id):
    """Subir archivo a espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Verificar que el alumno es miembro activo del espacio
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        flash('⚠️ No tienes permiso para subir archivos a este espacio', 'danger')
        return redirect(url_for('alumno.ver_espacio_colaborativo', espacio_id=espacio_id))
    
    if 'archivo' not in request.files:
        flash('No se subió ningún archivo', 'danger')
        return redirect(url_for('alumno.ver_espacio_colaborativo', espacio_id=espacio_id))
    
    archivo = request.files['archivo']
    descripcion = request.form.get('descripcion', '').strip()
    
    if archivo.filename == '':
        flash('Ningún archivo seleccionado', 'danger')
        return redirect(url_for('alumno.ver_espacio_colaborativo', espacio_id=espacio_id))
    
    try:
        # Guardar archivo
        ruta_archivo, es_s3 = guardar_archivo(archivo)
        
        # Crear registro en base de datos
        nuevo_archivo = ArchivoColaborativo(
            espacio_id=espacio_id,
            alumno_id=alumno_id,
            nombre_original=archivo.filename,
            nombre_archivo=os.path.basename(ruta_archivo),
            archivo_url=ruta_archivo,
            descripcion=descripcion if descripcion else None,
            tipo_contenido=archivo.content_type
        )
        
        db.session.add(nuevo_archivo)
        db.session.commit()
        
        flash('✅ Archivo subido correctamente al espacio colaborativo', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error al subir archivo: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.ver_espacio_colaborativo', espacio_id=espacio_id))


@alumno_bp.route('/espacios-colaborativos/<int:espacio_id>/nueva-idea', methods=['POST'])
@require_alumno
def nueva_idea_colaborativa(espacio_id):
    """Agregar nueva idea al espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Verificar que el alumno es miembro activo del espacio
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        return jsonify({'success': False, 'error': 'No tienes permiso'}), 403
    
    titulo = request.form.get('titulo', '').strip()
    contenido = request.form.get('contenido', '').strip()
    
    if not titulo or not contenido:
        return jsonify({'success': False, 'error': 'Título y contenido son obligatorios'}), 400
    
    try:
        # Crear nueva idea
        nueva_idea = IdeaColaborativa(
            espacio_id=espacio_id,
            alumno_id=alumno_id,
            titulo=titulo,
            contenido=contenido
        )
        
        db.session.add(nueva_idea)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '💡 Idea publicada correctamente',
            'idea_id': nueva_idea.id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@alumno_bp.route('/espacios-colaborativos/<int:espacio_id>/idea/<int:idea_id>/votar', methods=['POST'])
@require_alumno
def votar_idea_colaborativa(espacio_id, idea_id):
    """Votar por una idea en el espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Verificar que el alumno es miembro activo del espacio
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        return jsonify({'success': False, 'error': 'No tienes permiso'}), 403
    
    # Obtener la idea
    idea = IdeaColaborativa.query.get_or_404(idea_id)
    
    if idea.espacio_id != espacio_id:
        return jsonify({'success': False, 'error': 'Idea no pertenece a este espacio'}), 400
    
    try:
        # Incrementar votos
        idea.votos += 1
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '✅ Voto registrado',
            'nuevos_votos': idea.votos
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@alumno_bp.route('/espacios-colaborativos/invitacion/<int:invitacion_id>/aceptar', methods=['POST'])
@require_alumno
def aceptar_invitacion_espacio(invitacion_id):
    """Aceptar invitación a espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Obtener invitación
    invitacion = MiembroEspacio.query.get_or_404(invitacion_id)
    
    # Verificar que la invitación es para este alumno
    if invitacion.alumno_id != alumno_id:
        flash('⚠️ Esta invitación no es para ti', 'danger')
        return redirect(url_for('alumno.mis_espacios_colaborativos'))
    
    # Verificar que la invitación esté pendiente
    if invitacion.activo:
        flash('⚠️ Esta invitación ya fue aceptada', 'warning')
        return redirect(url_for('alumno.mis_espacios_colaborativos'))
    
    try:
        # Activar membresía
        invitacion.activo = True
        invitacion.fecha_aceptacion = datetime.utcnow()
        
        db.session.commit()
        
        # Obtener nombre del espacio para el mensaje
        espacio = EspacioColaborativo.query.get(invitacion.espacio_id)
        
        flash(f'🎉 ¡Te has unido al espacio "{espacio.nombre}"!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error al aceptar invitación: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.mis_espacios_colaborativos'))


@alumno_bp.route('/espacios-colaborativos/invitacion/<int:invitacion_id>/rechazar', methods=['POST'])
@require_alumno
def rechazar_invitacion_espacio(invitacion_id):
    """Rechazar invitación a espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Obtener invitación
    invitacion = MiembroEspacio.query.get_or_404(invitacion_id)
    
    # Verificar que la invitación es para este alumno
    if invitacion.alumno_id != alumno_id:
        flash('⚠️ Esta invitación no es para ti', 'danger')
        return redirect(url_for('alumno.mis_espacios_colaborativos'))
    
    try:
        # Eliminar invitación (o marcar como rechazada)
        db.session.delete(invitacion)
        db.session.commit()
        
        flash('Invitación rechazada', 'info')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error al rechazar invitación: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.mis_espacios_colaborativos'))


@alumno_bp.route('/espacios-colaborativos/<int:espacio_id>/abandonar', methods=['POST'])
@require_alumno
def abandonar_espacio_colaborativo(espacio_id):
    """Abandonar un espacio colaborativo"""
    alumno_id = session.get('alumno_id')
    
    # Obtener membresía
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        flash('⚠️ No eres miembro de este espacio', 'danger')
        return redirect(url_for('alumno.mis_espacios_colaborativos'))
    
    # Verificar que no es el creador (el creador no puede abandonar, solo eliminar)
    espacio = EspacioColaborativo.query.get(espacio_id)
    if espacio.creado_por_alumno_id == alumno_id:
        flash('⚠️ Como creador del espacio, no puedes abandonarlo. Puedes eliminarlo si lo deseas.', 'warning')
        return redirect(url_for('alumno.ver_espacio_colaborativo', espacio_id=espacio_id))
    
    try:
        # Eliminar membresía
        db.session.delete(miembro)
        db.session.commit()
        
        flash('👋 Has abandonado el espacio colaborativo', 'info')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error al abandonar espacio: {str(e)}', 'danger')
    
    return redirect(url_for('alumno.mis_espacios_colaborativos'))


@alumno_bp.route('/api/espacios-colaborativos/<int:espacio_id>/ideas')
@require_alumno
def obtener_ideas_espacio(espacio_id):
    """API para obtener ideas de un espacio (para actualización AJAX)"""
    alumno_id = session.get('alumno_id')
    
    # Verificar que el alumno es miembro activo del espacio
    miembro = MiembroEspacio.query.filter_by(
        espacio_id=espacio_id,
        alumno_id=alumno_id,
        activo=True
    ).first()
    
    if not miembro:
        return jsonify({'error': 'No tienes acceso'}), 403
    
    # Obtener ideas ordenadas por votos y fecha
    ideas = IdeaColaborativa.query.filter_by(
        espacio_id=espacio_id
    ).order_by(
        IdeaColaborativa.votos.desc(),
        IdeaColaborativa.fecha_creacion.desc()
    ).all()
    
    ideas_json = []
    for idea in ideas:
        ideas_json.append({
            'id': idea.id,
            'titulo': idea.titulo,
            'contenido': idea.contenido,
            'votos': idea.votos,
            'autor': idea.alumno.nombre_completo,
            'fecha': idea.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
            'puedo_votar': idea.alumno_id != alumno_id  # No puede votar por su propia idea
        })
    
    return jsonify({'ideas': ideas_json})


@alumno_bp.route('/api/espacios-colaborativos/notificaciones')
@require_alumno
def notificaciones_espacios_colaborativos():
    """API para obtener notificaciones de espacios colaborativos"""
    alumno_id = session.get('alumno_id')
    
    # Contar invitaciones pendientes
    invitaciones_pendientes = MiembroEspacio.query.filter_by(
        alumno_id=alumno_id,
        activo=False
    ).count()
    
    return jsonify({
        'invitaciones_pendientes': invitaciones_pendientes
    })

@alumno_bp.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente.')
    return redirect(url_for('index'))  # ← CORREGIDO: 'index' en lugar de 'public.index'