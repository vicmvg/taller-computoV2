# web/routes/admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify, send_from_directory, current_app
from web.models import (Equipo, Mantenimiento, Anuncio, UsuarioAlumno, 
                        EntregaAlumno, Asistencia, Pago, ReciboPago, SolicitudArchivo,
                        ArchivoEnviado, ReporteAsistencia, ActividadGrado, Cuestionario,
                        BancoCuestionario, Horario, Plataforma, Mensaje, MensajeFlotante,
                        MensajeLeido, Configuracion, Recurso, CriterioBoleta, BoletaGenerada,
                        Encuesta, RespuestaEncuesta)  # ✅ Agregados Encuesta y RespuestaEncuesta
from web.extensions import db
from web.utils import require_profesor, s3_manager, guardar_archivo, generar_qr_img, log_error, log_info, generar_pdf_boleta, descargar_archivo, FileValidator
from datetime import datetime, timedelta, date  # ✅ Agregué 'date' aquí
import os
from sqlalchemy import func, case, text
from io import BytesIO
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import shutil
from werkzeug.utils import secure_filename
from flask_login import login_required  # ✅ Este ya está importado

# Definimos el Blueprint para el administrador
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- DASHBOARD Y RUTAS PRINCIPALES ---
@admin_bp.route('/')
@admin_bp.route('/dashboard')
@require_profesor
def dashboard():
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

# --- GESTIÓN DE ALUMNOS ---
@admin_bp.route('/alumnos')
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

@admin_bp.route('/alumnos/agregar', methods=['POST'])
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
        return redirect(url_for('admin.gestionar_alumnos'))
    
    from werkzeug.security import generate_password_hash
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
    return redirect(url_for('admin.gestionar_alumnos'))

@admin_bp.route('/alumnos/editar/<int:id>', methods=['POST'])
@require_profesor
def editar_alumno(id):
    alumno = UsuarioAlumno.query.get_or_404(id)
    
    alumno.nombre_completo = request.form['nombre_completo']
    alumno.grado_grupo = request.form['grado_grupo']
    alumno.activo = 'activo' in request.form
    
    nueva_password = request.form.get('password')
    if nueva_password:
        from werkzeug.security import generate_password_hash
        alumno.password_hash = generate_password_hash(nueva_password)
    
    db.session.commit()
    
    flash(f'Datos de {alumno.nombre_completo} actualizados.', 'success')
    return redirect(url_for('admin.gestionar_alumnos'))

@admin_bp.route('/alumnos/eliminar/<int:id>')
@require_profesor
def eliminar_alumno(id):
    alumno = UsuarioAlumno.query.get_or_404(id)
    nombre = alumno.nombre_completo
    
    db.session.delete(alumno)
    db.session.commit()
    
    flash(f'Alumno {nombre} eliminado del sistema.', 'warning')
    return redirect(url_for('admin.gestionar_alumnos'))

# --- ASISTENCIA ---
@admin_bp.route('/asistencia/tomar', methods=['POST'])
@require_profesor
def tomar_asistencia():
    fecha_str = request.form.get('fecha', datetime.now().strftime('%Y-%m-%d'))
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
    return redirect(url_for('admin.gestionar_alumnos', grado=request.form.get('grado_origen')))

@admin_bp.route('/reporte-asistencia/<grupo>')
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
        
    except Exception as e:
        log_error(f"Error al generar reporte: {str(e)}")
        flash(f'❌ Error al generar reporte: {str(e)}', 'danger')
        return redirect(url_for('admin.gestionar_alumnos'))

def generar_pdf_asistencia(grupo, fecha_inicio, fecha_fin=None):
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
            except Exception as e:
                log_error(f"No se pudo guardar en S3: {str(e)}")
        
        os.makedirs(os.path.join('uploads', 'reportes'), exist_ok=True)
        local_path = os.path.join('uploads', 'reportes', filename)
        
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
        raise Exception(f"Error al generar reporte: {str(e)}")

@admin_bp.route('/reportes-asistencia')
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

@admin_bp.route('/descargar-reporte/<int:reporte_id>')
@require_profesor
def descargar_reporte_guardado(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    if not reporte.archivo_url:
        flash('El archivo de este reporte no está disponible', 'danger')
        return redirect(url_for('admin.ver_reportes_asistencia'))
    
    try:
        if reporte.archivo_url and reporte.archivo_url.startswith('http') and s3_manager.is_configured:
            file_stream, content_type = s3_manager.download_file(reporte.archivo_url)
            return send_file(file_stream, mimetype=content_type, 
                           as_attachment=True, download_name=reporte.nombre_archivo)
        
        return send_from_directory(os.path.join('uploads', 'reportes'), 
                                  reporte.nombre_archivo, as_attachment=True)
    except Exception as e:
        log_error(f"Error al descargar reporte: {str(e)}")
        flash(f'Error: El archivo no existe o fue eliminado', 'danger')
        return redirect(url_for('admin.ver_reportes_asistencia'))

@admin_bp.route('/eliminar-reporte/<int:reporte_id>')
@require_profesor
def eliminar_reporte(reporte_id):
    reporte = ReporteAsistencia.query.get_or_404(reporte_id)
    
    try:
        if reporte.archivo_url and reporte.archivo_url.startswith('http') and s3_manager.is_configured:
            try:
                key = f"reportes/{reporte.nombre_archivo}"
                s3_manager.delete_file(key)
            except Exception as e:
                log_error(f"No se pudo eliminar de S3: {e}")
        
        db.session.delete(reporte)
        db.session.commit()
        
        flash('Reporte eliminado correctamente', 'success')
        
    except Exception as e:
        log_error(f"Error al eliminar reporte: {str(e)}")
        flash(f'Error al eliminar reporte: {str(e)}', 'danger')
    
    return redirect(url_for('admin.ver_reportes_asistencia'))

# --- ENTREGAS DE ALUMNOS ---
@admin_bp.route('/alumnos/entregas')
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

# --- GESTIÓN DE ENTREGAS (NUEVA RUTA) ---
@admin_bp.route('/entregas')
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

@admin_bp.route('/alumnos/calificar/<int:id>', methods=['POST'])
@require_profesor
def calificar_entrega(id):
    entrega = EntregaAlumno.query.get_or_404(id)
    entrega.estrellas = int(request.form['estrellas'])
    entrega.comentarios = request.form['comentarios']
    
    db.session.commit()
    
    flash(f'Entrega de {entrega.nombre_alumno} calificada con {entrega.estrellas} estrellas.', 'success')
    return redirect(url_for('admin.ver_entregas_alumnos'))

# --- INVENTARIO ---
@admin_bp.route('/inventario')
@require_profesor
def inventario():
    equipos = Equipo.query.order_by(Equipo.id.desc()).all()
    return render_template('admin/inventario.html', equipos=equipos)

@admin_bp.route('/inventario/agregar', methods=['POST'])
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
    return redirect(url_for('admin.inventario'))

@admin_bp.route('/inventario/eliminar/<int:id>')
@require_profesor
def eliminar_equipo(id):
    equipo = Equipo.query.get_or_404(id)
    
    db.session.delete(equipo)
    db.session.commit()
    
    flash('Equipo eliminado del inventario', 'warning')
    return redirect(url_for('admin.inventario'))

@admin_bp.route('/generar_qr_img/<int:id>')
@require_profesor
def generar_qr_img_admin(id):
    equipo = Equipo.query.get_or_404(id)
    info_qr = f"PROPIEDAD ESCUELA MARIANO ESCOBEDO\nID: {equipo.id}\nTipo: {equipo.tipo}\nMarca: {equipo.marca}\nModelo: {equipo.modelo}"
    
    img_io = generar_qr_img(info_qr)
    return send_file(img_io, mimetype='image/png')

# --- MANTENIMIENTO ---
@admin_bp.route('/mantenimiento')
@require_profesor
def mantenimiento():
    pendientes = Mantenimiento.query.filter_by(fecha_reparacion=None).all()
    historial = Mantenimiento.query.filter(Mantenimiento.fecha_reparacion != None).order_by(Mantenimiento.fecha_reparacion.desc()).limit(10).all()
    equipos = Equipo.query.all()
    
    return render_template('admin/mantenimiento.html', pendientes=pendientes, historial=historial, equipos=equipos)

@admin_bp.route('/mantenimiento/reportar', methods=['POST'])
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
    return redirect(url_for('admin.mantenimiento'))

@admin_bp.route('/mantenimiento/solucionar', methods=['POST'])
@require_profesor
def solucionar_falla():
    reporte_id = request.form['reporte_id']
    solucion = request.form['solucion']
    
    reporte = Mantenimiento.query.get(reporte_id)
    reporte.fecha_reparacion = datetime.now()
    reporte.solucion = solucion
    reporte.equipo.estado = "Funcional"
    
    db.session.commit()
    flash('¡Equipo reparado exitosamente!', 'success')
    return redirect(url_for('admin.mantenimiento'))

# --- ANUNCIOS ---
@admin_bp.route('/anuncios')
@require_profesor
def gestionar_anuncios():
    anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).all()
    return render_template('admin/anuncios.html', anuncios=anuncios)

@admin_bp.route('/anuncios/publicar', methods=['POST'])
@require_profesor
def publicar_anuncio():
    titulo = request.form['titulo']
    contenido = request.form['contenido']
    
    nuevo_anuncio = Anuncio(titulo=titulo, contenido=contenido)
    
    db.session.add(nuevo_anuncio)
    db.session.commit()
    
    flash('¡Anuncio publicado en la página principal!', 'success')
    return redirect(url_for('admin.gestionar_anuncios'))

@admin_bp.route('/anuncios/eliminar/<int:id>')
@require_profesor
def eliminar_anuncio(id):
    anuncio = Anuncio.query.get_or_404(id)
    
    db.session.delete(anuncio)
    db.session.commit()
    
    flash('Anuncio eliminado.', 'secondary')
    return redirect(url_for('admin.gestionar_anuncios'))

# --- CUESTIONARIOS ---
@admin_bp.route('/cuestionarios')
@require_profesor
def gestionar_cuestionarios():
    cuestionarios = Cuestionario.query.order_by(Cuestionario.fecha.desc()).all()
    return render_template('admin/cuestionarios.html', cuestionarios=cuestionarios)

@admin_bp.route('/cuestionarios/publicar', methods=['POST'])
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
    return redirect(url_for('admin.gestionar_cuestionarios'))

@admin_bp.route('/cuestionarios/eliminar/<int:id>')
@require_profesor
def eliminar_cuestionario(id):
    item = Cuestionario.query.get_or_404(id)
    
    db.session.delete(item)
    db.session.commit()
    
    flash('Cuestionario eliminado.', 'secondary')
    return redirect(url_for('admin.gestionar_cuestionarios'))

# --- BANCO DE CUESTIONARIOS ---
@admin_bp.route('/banco')
@require_profesor
def gestionar_banco():
    banco = BancoCuestionario.query.order_by(BancoCuestionario.fecha_creacion.desc()).all()
    return render_template('admin/Banco_cuestionarios.html', banco=banco)

@admin_bp.route('/banco/agregar', methods=['POST'])
@require_profesor
def agregar_al_banco():
    nuevo = BancoCuestionario(
        titulo=request.form['titulo'],
        url=request.form['url']
    )
    
    db.session.add(nuevo)
    db.session.commit()
    
    flash('Cuestionario guardado en la bodega.', 'success')
    return redirect(url_for('admin.gestionar_banco'))

@admin_bp.route('/banco/eliminar/<int:id>')
@require_profesor
def eliminar_del_banco(id):
    item = BancoCuestionario.query.get_or_404(id)
    
    db.session.delete(item)
    db.session.commit()
    
    flash('Plantilla eliminada.', 'warning')
    return redirect(url_for('admin.gestionar_banco'))

@admin_bp.route('/banco/asignar', methods=['POST'])
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
        
    return redirect(url_for('admin.gestionar_banco'))

# --- GRADOS ---
@admin_bp.route('/grados', methods=['GET', 'POST'])
@require_profesor
def gestionar_grados():
    """Gestionar planeación semanal por grado - VERSIÓN MEJORADA"""
    
    if request.method == 'POST':
        try:
            # Obtener datos del formulario
            grado_id = int(request.form['grado'])
            titulo = request.form.get('titulo', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            
            # Nuevos campos
            numero_semana = request.form.get('numero_semana', '').strip()
            fecha_inicio = request.form.get('fecha_inicio', '').strip()
            fecha_fin = request.form.get('fecha_fin', '').strip()
            objetivos = request.form.get('objetivos', '').strip()
            material_necesario = request.form.get('material_necesario', '').strip()
            tareas = request.form.get('tareas', '').strip()
            observaciones = request.form.get('observaciones', '').strip()
            
            # Validar que al menos el título esté presente
            if not titulo:
                flash('El título es obligatorio', 'warning')
                return redirect(url_for('admin.gestionar_grados'))
            
            # Buscar o crear actividad
            actividad = ActividadGrado.query.filter_by(grado=grado_id).first()
            
            if not actividad:
                actividad = ActividadGrado(grado=grado_id)
            
            # Actualizar campos básicos
            actividad.titulo = titulo
            actividad.descripcion = descripcion if descripcion else None
            
            # Actualizar campos de semana
            actividad.numero_semana = int(numero_semana) if numero_semana else None
            
            # Convertir fechas
            if fecha_inicio:
                try:
                    actividad.fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                except:
                    actividad.fecha_inicio = None
            else:
                actividad.fecha_inicio = None
                
            if fecha_fin:
                try:
                    actividad.fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
                except:
                    actividad.fecha_fin = None
            else:
                actividad.fecha_fin = None
            
            # Actualizar campos de contenido
            actividad.objetivos = objetivos if objetivos else None
            actividad.material_necesario = material_necesario if material_necesario else None
            actividad.tareas = tareas if tareas else None
            actividad.observaciones = observaciones if observaciones else None
            
            # Metadatos
            actividad.fecha_actualizacion = datetime.now()
            actividad.actualizado_por = session.get('user', 'Admin')
            
            # Guardar
            if actividad not in db.session:
                db.session.add(actividad)
            
            db.session.commit()
            
            flash(f'✅ Planeación de {grado_id}° grado guardada correctamente', 'success')
            return redirect(url_for('admin.gestionar_grados'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar: {str(e)}', 'danger')
            return redirect(url_for('admin.gestionar_grados'))
    
    # GET - Mostrar formulario
    actividades = ActividadGrado.query.all()
    info_grados = {a.grado: a for a in actividades}
    
    return render_template('admin/gestionar_grados.html', info_grados=info_grados)

# --- HORARIOS ---
@admin_bp.route('/horarios')
@require_profesor
def gestionar_horarios():
    horarios = Horario.query.all()
    return render_template('admin/horarios.html', horarios=horarios)

@admin_bp.route('/horarios/agregar', methods=['POST'])
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
    return redirect(url_for('admin.gestionar_horarios'))

@admin_bp.route('/horarios/eliminar/<int:id>')
@require_profesor
def eliminar_horario(id):
    horario = Horario.query.get_or_404(id)
    
    db.session.delete(horario)
    db.session.commit()
    
    flash('Horario eliminado.', 'warning')
    return redirect(url_for('admin.gestionar_horarios'))

# --- PLATAFORMAS ---
@admin_bp.route('/plataformas')
@require_profesor
def gestionar_plataformas():
    plataformas = Plataforma.query.all()
    return render_template('admin/plataformas.html', plataformas=plataformas)

@admin_bp.route('/plataformas/agregar', methods=['POST'])
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
    return redirect(url_for('admin.gestionar_plataformas'))

@admin_bp.route('/plataformas/eliminar/<int:id>')
@require_profesor
def eliminar_plataforma(id):
    p = Plataforma.query.get_or_404(id)
    
    db.session.delete(p)
    db.session.commit()
    
    flash('Plataforma eliminada.', 'warning')
    return redirect(url_for('admin.gestionar_plataformas'))

# --- RECURSOS ---
@admin_bp.route('/recursos')
@require_profesor
def gestionar_recursos():
    recursos = Recurso.query.order_by(Recurso.fecha.desc()).all()
    return render_template('admin/recursos.html', recursos=recursos)

@admin_bp.route('/recursos/subir', methods=['POST'])
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
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            
    return redirect(url_for('admin.gestionar_recursos'))

@admin_bp.route('/recursos/eliminar/<int:id>')
@require_profesor
def eliminar_recurso(id):
    recurso = Recurso.query.get_or_404(id)
    
    db.session.delete(recurso)
    db.session.commit()
    
    flash('Recurso eliminado de la lista.', 'warning')
    return redirect(url_for('admin.gestionar_recursos'))

@admin_bp.route('/recursos/ver/<path:archivo_path>')
@require_profesor
def ver_archivo(archivo_path):
    """Ver archivos PDF o Word desde recursos"""
    try:
        if archivo_path.startswith('uploads/'):
            # Si usa S3
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
                return redirect(url_for('admin.gestionar_recursos'))
        
        else:
            # Archivo local
            filename = archivo_path
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            
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
                return redirect(url_for('admin.gestionar_recursos'))
                
    except Exception as e:
        flash(f'Error al cargar el archivo: {str(e)}', 'danger')
        return redirect(url_for('admin.gestionar_recursos'))

# =============================================================================
# ENCUESTAS DE RETROALIMENTACIÓN
# =============================================================================

@admin_bp.route('/encuestas')
@require_profesor
def gestionar_encuestas():
    """Ver todas las encuestas creadas"""
    encuestas = Encuesta.query.order_by(Encuesta.fecha_creacion.desc()).all()
    
    # Calcular estadísticas para cada encuesta
    for encuesta in encuestas:
        encuesta.total_respuestas_count = encuesta.total_respuestas()
        
        # Calcular promedio general si hay respuestas
        if encuesta.total_respuestas_count > 0:
            respuestas = RespuestaEncuesta.query.filter_by(encuesta_id=encuesta.id).all()
            promedios = [r.promedio_respuestas() for r in respuestas]
            encuesta.promedio_general = sum(promedios) / len(promedios)
        else:
            encuesta.promedio_general = 0
    
    return render_template('admin/encuestas.html', encuestas=encuestas)


@admin_bp.route('/encuestas/crear', methods=['GET', 'POST'])
@require_profesor
def crear_encuesta():
    """Crear nueva encuesta"""
    if request.method == 'POST':
        try:
            titulo = request.form.get('titulo', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            grupos = request.form.getlist('grupos')  # Lista de grupos seleccionados
            fecha_cierre = request.form.get('fecha_cierre', '').strip()
            obligatoria = request.form.get('obligatoria') == 'on'
            
            if not titulo:
                flash('El título es obligatorio', 'warning')
                return redirect(url_for('admin.crear_encuesta'))
            
            # Procesar grupos destino
            if 'todos' in grupos:
                grupos_destino = 'todos'
            else:
                grupos_destino = ','.join(grupos)
            
            if not grupos_destino:
                flash('Debes seleccionar al menos un grupo', 'warning')
                return redirect(url_for('admin.crear_encuesta'))
            
            # Convertir fecha de cierre
            fecha_cierre_dt = None
            if fecha_cierre:
                try:
                    fecha_cierre_dt = datetime.strptime(fecha_cierre, '%Y-%m-%d')
                except:
                    pass
            
            # Crear encuesta
            nueva_encuesta = Encuesta(
                titulo=titulo,
                descripcion=descripcion,
                grupos_destino=grupos_destino,
                activa=True,
                obligatoria=obligatoria,
                fecha_cierre=fecha_cierre_dt,
                creado_por=session.get('user', 'Admin')
            )
            
            db.session.add(nueva_encuesta)
            db.session.commit()
            
            flash(f'✅ Encuesta "{titulo}" creada y enviada correctamente', 'success')
            return redirect(url_for('admin.gestionar_encuestas'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear encuesta: {str(e)}', 'danger')
            return redirect(url_for('admin.crear_encuesta'))
    
    # GET - Mostrar formulario
    # Obtener lista de grupos únicos
    grupos_unicos = db.session.query(UsuarioAlumno.grado_grupo)\
        .filter_by(activo=True)\
        .distinct()\
        .order_by(UsuarioAlumno.grado_grupo)\
        .all()
    grupos = [g[0] for g in grupos_unicos]
    
    return render_template('admin/crear_encuesta.html', grupos=grupos)


@admin_bp.route('/encuestas/<int:encuesta_id>/resultados')
@require_profesor
def ver_resultados_encuesta(encuesta_id):
    """Ver resultados detallados de una encuesta"""
    encuesta = Encuesta.query.get_or_404(encuesta_id)
    respuestas = RespuestaEncuesta.query.filter_by(encuesta_id=encuesta_id)\
        .order_by(RespuestaEncuesta.fecha_respuesta.desc()).all()
    
    # Calcular estadísticas
    stats = {
        'total_respuestas': len(respuestas),
        'promedio_clases': 0,
        'promedio_aprendizaje': 0,
        'promedio_maestro': 0,
        'promedio_contenido': 0,
        'promedio_dificultad': 0,
        'promedio_general': 0
    }
    
    if respuestas:
        stats['promedio_clases'] = sum(r.pregunta1_clases for r in respuestas) / len(respuestas)
        stats['promedio_aprendizaje'] = sum(r.pregunta2_aprendizaje for r in respuestas) / len(respuestas)
        stats['promedio_maestro'] = sum(r.pregunta3_maestro for r in respuestas) / len(respuestas)
        stats['promedio_contenido'] = sum(r.pregunta4_contenido for r in respuestas) / len(respuestas)
        stats['promedio_dificultad'] = sum(r.pregunta5_dificultad for r in respuestas) / len(respuestas)
        
        # Promedio general
        promedios = [r.promedio_respuestas() for r in respuestas]
        stats['promedio_general'] = sum(promedios) / len(promedios)
    
    # Agrupar comentarios
    comentarios_positivos = [r for r in respuestas if r.comentario_positivo]
    comentarios_mejora = [r for r in respuestas if r.comentario_mejora]
    comentarios_adicionales = [r for r in respuestas if r.comentario_adicional]
    
    return render_template('admin/resultados_encuesta.html',
                         encuesta=encuesta,
                         respuestas=respuestas,
                         stats=stats,
                         comentarios_positivos=comentarios_positivos,
                         comentarios_mejora=comentarios_mejora,
                         comentarios_adicionales=comentarios_adicionales)


@admin_bp.route('/encuestas/<int:encuesta_id>/toggle')
@require_profesor
def toggle_encuesta(encuesta_id):
    """Activar/desactivar una encuesta"""
    encuesta = Encuesta.query.get_or_404(encuesta_id)
    encuesta.activa = not encuesta.activa
    db.session.commit()
    
    estado = "activada" if encuesta.activa else "desactivada"
    flash(f'Encuesta {estado} correctamente', 'success')
    return redirect(url_for('admin.gestionar_encuestas'))


@admin_bp.route('/encuestas/<int:encuesta_id>/eliminar')
@require_profesor
def eliminar_encuesta(encuesta_id):
    """Eliminar una encuesta y todas sus respuestas"""
    encuesta = Encuesta.query.get_or_404(encuesta_id)
    titulo = encuesta.titulo
    
    db.session.delete(encuesta)
    db.session.commit()
    
    flash(f'Encuesta "{titulo}" eliminada correctamente', 'success')
    return redirect(url_for('admin.gestionar_encuestas'))

# --- CHAT ---
@admin_bp.route('/chat/toggle')
@require_profesor
def toggle_chat():
    config = Configuracion.query.get('chat_activo')
    if not config:
        config = Configuracion(clave='chat_activo', valor='True')
        db.session.add(config)
    
    if config.valor == 'True':
        config.valor = 'False'
        mensaje = 'Chat desactivado para todos los alumnos.'
        tipo = 'secondary'
    else:
        config.valor = 'True'
        mensaje = 'Chat activado. Los alumnos pueden conversar.'
        tipo = 'success'
    
    db.session.commit()
    flash(mensaje, tipo)
    return redirect(url_for('admin.dashboard'))

# --- MENSAJES FLOTANTES ---
@admin_bp.route('/mensajes-flotantes')
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

@admin_bp.route('/mensajes-flotantes/crear', methods=['POST'])
@require_profesor
def crear_mensaje_flotante():
    grado = request.form.get('grado')
    grupo = request.form.get('grupo')
    contenido = request.form.get('contenido')
    
    if not contenido or not grado or not grupo:
        flash('Debes completar todos los campos', 'danger')
        return redirect(url_for('admin.gestionar_mensajes_flotantes'))
    
    grado_grupo = f"{grado}{grupo}"
    
    nuevo_mensaje = MensajeFlotante(
        grado_grupo=grado_grupo,
        contenido=contenido,
        creado_por=session.get('user', 'Sistema')
    )
    
    db.session.add(nuevo_mensaje)
    db.session.commit()
    
    flash(f'¡Mensaje enviado al grupo {grado_grupo}!', 'success')
    return redirect(url_for('admin.gestionar_mensajes_flotantes'))

@admin_bp.route('/mensajes-flotantes/desactivar/<int:id>')
@require_profesor
def desactivar_mensaje_flotante(id):
    mensaje = MensajeFlotante.query.get_or_404(id)
    mensaje.activo = False
    db.session.commit()
    
    flash('Mensaje desactivado correctamente', 'success')
    return redirect(url_for('admin.gestionar_mensajes_flotantes'))

# --- BOLETAS ---
@admin_bp.route('/boletas/config', methods=['GET', 'POST'])
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

@admin_bp.route('/boletas/borrar-criterio/<int:id>')
@require_profesor
def borrar_criterio(id):
    c = CriterioBoleta.query.get_or_404(id)
    
    db.session.delete(c)
    db.session.commit()
    
    return redirect(url_for('admin.configurar_boletas'))

@admin_bp.route('/boletas/generar', methods=['GET', 'POST'])
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
            
        except Exception as e:
            log_error(f"Error al generar boleta: {str(e)}")
            flash(f'Error al generar boleta: {str(e)}', 'danger')
            return redirect(url_for('admin.generar_boleta'))

    return render_template('admin/boleta_form.html', 
                         alumnos=alumnos, 
                         alumno_seleccionado=alumno, 
                         criterios=criterios,
                         filtro_actual=filtro_grado)

@admin_bp.route('/boletas/historial')
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

@admin_bp.route('/boletas/descargar/<int:boleta_id>')
@require_profesor
def descargar_boleta_guardada(boleta_id):
    boleta = BoletaGenerada.query.get_or_404(boleta_id)
    return descargar_archivo(boleta.archivo_url, boleta.nombre_archivo, 'boletas')

@admin_bp.route('/boletas/eliminar/<int:boleta_id>')
@require_profesor
def eliminar_boleta_guardada(boleta_id):
    boleta = BoletaGenerada.query.get_or_404(boleta_id)
    
    try:
        if boleta.archivo_url and boleta.archivo_url.startswith('boletas/') and s3_manager.is_configured:
            try:
                s3_manager.delete_file(boleta.archivo_url)
            except Exception as e:
                log_error(f"No se pudo eliminar de S3: {e}")
        
        db.session.delete(boleta)
        db.session.commit()
        
        flash('Boleta eliminada correctamente', 'success')
        
    except Exception as e:
        log_error(f"Error al eliminar boleta: {str(e)}")
        flash(f'Error al eliminar boleta: {str(e)}', 'danger')
    
    return redirect(url_for('admin.ver_boletas_historial'))

# --- PAGOS ---
@admin_bp.route('/pagos')
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

# --- NUEVAS RUTAS DE PAGOS ---
@admin_bp.route('/pagos/crear', methods=['GET', 'POST'])
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
            return redirect(url_for('admin.gestionar_pagos'))
            
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

@admin_bp.route('/pagos/<int:pago_id>/registrar-pago', methods=['POST'])
@require_profesor
def registrar_pago(pago_id):
    pago = Pago.query.get_or_404(pago_id)
    
    try:
        monto_pagado = float(request.form.get('monto_pagado'))
        metodo_pago = request.form.get('metodo_pago')
        observaciones = request.form.get('observaciones', '')
        
        if monto_pagado <= 0:
            flash('El monto debe ser mayor a 0', 'danger')
            return redirect(url_for('admin.gestionar_pagos'))
        
        if monto_pagado > pago.monto_pendiente:
            flash(f'El monto no puede ser mayor al pendiente (${pago.monto_pendiente:,.2f})', 'danger')
            return redirect(url_for('admin.gestionar_pagos'))
        
        fecha_actual = datetime.now()
        numero_recibo = f"REC-{fecha_actual.strftime('%Y%m%d%H%M%S')}-{pago.id}"
        
        # Función para generar recibo PDF (importada desde utils o definida aquí)
        from web.utils import generar_recibo_pdf
        buffer_pdf = generar_recibo_pdf(numero_recibo, pago, monto_pagado, metodo_pago, observaciones, session.get('user', 'Sistema'))
        
        nombre_archivo = f"recibo_{numero_recibo}.pdf"
        key_s3 = f"pagos/recibos/{pago.grado_grupo}/{nombre_archivo}"
        
        # Crear recibo
        nuevo_recibo = ReciboPago(
            pago_id=pago.id,
            numero_recibo=numero_recibo,
            monto=monto_pagado,
            metodo_pago=metodo_pago,
            recibido_por=session.get('user', 'Sistema'),
            observaciones=observaciones,
            nombre_archivo=nombre_archivo
        )
        
        # Subir a S3
        if s3_manager.is_configured:
            try:
                file_url = s3_manager.upload_file(buffer_pdf, key_s3, 'application/pdf')
                nuevo_recibo.archivo_url = key_s3
            except Exception as e:
                log_warning(f"No se pudo subir a S3: {e}")
                # Guardar localmente
                ruta_local = os.path.join('uploads', 'pagos', 'recibos')
                os.makedirs(ruta_local, exist_ok=True)
                with open(os.path.join(ruta_local, nombre_archivo), 'wb') as f:
                    f.write(buffer_pdf.getvalue())
                nuevo_recibo.archivo_url = f"pagos/recibos/{nombre_archivo}"
        else:
            # Guardar localmente
            ruta_local = os.path.join('uploads', 'pagos', 'recibos')
            os.makedirs(ruta_local, exist_ok=True)
            with open(os.path.join(ruta_local, nombre_archivo), 'wb') as f:
                f.write(buffer_pdf.getvalue())
            nuevo_recibo.archivo_url = f"pagos/recibos/{nombre_archivo}"
        
        db.session.add(nuevo_recibo)
        
        # Actualizar pago
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
        return redirect(url_for('admin.gestionar_pagos'))

@admin_bp.route('/pagos/recibos')
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

@admin_bp.route('/pagos/recibos/descargar/<int:recibo_id>')
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
                os.path.join('uploads', 'pagos', 'recibos'),
                recibo.nombre_archivo,
                as_attachment=True
            )
    except Exception as e:
        log_error(f"Error al descargar recibo: {str(e)}")
        flash(f'Error al descargar recibo: {str(e)}', 'danger')
        return redirect(url_for('admin.ver_recibos'))

@admin_bp.route('/pagos/<int:pago_id>/eliminar')
@require_profesor
def eliminar_pago(pago_id):
    pago = Pago.query.get_or_404(pago_id)
    
    try:
        # Eliminar archivos de S3 de los recibos
        recibos_a_eliminar = list(pago.recibos)
        
        for recibo in recibos_a_eliminar:
            if recibo.archivo_url and s3_manager.is_configured:
                try:
                    s3_manager.delete_file(recibo.archivo_url)
                    log_info(f"Archivo eliminado: {recibo.archivo_url}")