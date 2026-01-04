# web/events/chat_events.py
from flask import session, request
from flask_socketio import emit, join_room, leave_room, rooms
from web.extensions import socketio, db
from web.models import Mensaje, Configuracion, UsuarioAlumno
from web.utils import chat_moderator, log_info, log_error
from datetime import datetime

# Diccionario para rastrear usuarios conectados por grupo
usuarios_conectados = {}  # {grado_grupo: {sid: {nombre, alumno_id}}}
usuarios_escribiendo = {}  # {grado_grupo: {alumno_id: nombre}}


@socketio.on('connect')
def handle_connect():
    """Cuando un usuario se conecta al WebSocket"""
    # Verificar que sea un alumno autenticado
    if 'alumno_id' not in session:
        return False  # Rechazar conexión
    
    alumno_id = session.get('alumno_id')
    nombre = session.get('alumno_nombre')
    grado_grupo = session.get('alumno_grado')
    
    # Unir al room de su grupo
    join_room(grado_grupo)
    
    # Registrar usuario conectado
    if grado_grupo not in usuarios_conectados:
        usuarios_conectados[grado_grupo] = {}
    
    usuarios_conectados[grado_grupo][request.sid] = {
        'nombre': nombre,
        'alumno_id': alumno_id
    }
    
    log_info(f"✅ {nombre} ({grado_grupo}) conectado - SID: {request.sid}")
    
    # Notificar a todos en el grupo que alguien se conectó
    emit('usuario_conectado', {
        'nombre': nombre,
        'alumno_id': alumno_id,
        'total_conectados': len(usuarios_conectados[grado_grupo])
    }, room=grado_grupo, include_self=False)
    
    # Enviar lista de usuarios conectados al que acaba de entrar
    lista_usuarios = [
        {'nombre': u['nombre'], 'alumno_id': u['alumno_id']} 
        for u in usuarios_conectados[grado_grupo].values()
    ]
    emit('usuarios_conectados', {'usuarios': lista_usuarios})


@socketio.on('disconnect')
def handle_disconnect():
    """Cuando un usuario se desconecta"""
    if 'alumno_id' not in session:
        return
    
    alumno_id = session.get('alumno_id')
    nombre = session.get('alumno_nombre')
    grado_grupo = session.get('alumno_grado')
    
    # Remover de usuarios conectados
    if grado_grupo in usuarios_conectados:
        usuarios_conectados[grado_grupo].pop(request.sid, None)
        
        # Si no quedan usuarios, limpiar el grupo
        if not usuarios_conectados[grado_grupo]:
            del usuarios_conectados[grado_grupo]
    
    # Remover de "escribiendo"
    if grado_grupo in usuarios_escribiendo:
        usuarios_escribiendo[grado_grupo].pop(alumno_id, None)
    
    log_info(f"❌ {nombre} ({grado_grupo}) desconectado")
    
    # Notificar a todos en el grupo
    emit('usuario_desconectado', {
        'nombre': nombre,
        'alumno_id': alumno_id,
        'total_conectados': len(usuarios_conectados.get(grado_grupo, {}))
    }, room=grado_grupo)


@socketio.on('cargar_mensajes')
def handle_cargar_mensajes():
    """Cargar historial de mensajes del grupo"""
    if 'alumno_id' not in session:
        return
    
    grado_grupo = session.get('alumno_grado')
    alumno_id = session.get('alumno_id')
    
    # Verificar si el chat está activo
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False
    
    # Obtener mensajes del grupo
    mensajes = Mensaje.query.filter_by(
        grado_grupo=grado_grupo
    ).order_by(Mensaje.fecha.asc()).all()
    
    lista_mensajes = []
    for m in mensajes:
        es_mio = (m.alumno_id == alumno_id)
        lista_mensajes.append({
            'id': m.id,
            'nombre': 'Yo' if es_mio else m.nombre_alumno,
            'texto': m.contenido,
            'es_mio': es_mio,
            'hora': m.fecha.strftime('%H:%M'),
            'alumno_id': m.alumno_id
        })
    
    emit('mensajes_cargados', {
        'mensajes': lista_mensajes,
        'activo': chat_activo
    })


@socketio.on('enviar_mensaje')
def handle_enviar_mensaje(data):
    """Enviar un nuevo mensaje al chat"""
    if 'alumno_id' not in session:
        emit('error', {'msg': 'No autenticado'})
        return
    
    alumno_id = session.get('alumno_id')
    nombre = session.get('alumno_nombre')
    grado_grupo = session.get('alumno_grado')
    
    contenido = data.get('mensaje', '').strip()
    
    if not contenido:
        emit('error', {'msg': 'Mensaje vacío'})
        return
    
    # Verificar si el chat está activo
    config = Configuracion.query.get('chat_activo')
    chat_activo = True if not config or config.valor == 'True' else False
    
    if not chat_activo:
        emit('mensaje_bloqueado', {
            'tipo': 'chat_desactivado',
            'msg': '🔒 El chat está desactivado por el profesor'
        })
        return
    
    # 🛡️ MODERAR EL MENSAJE
    resultado_moderacion = chat_moderator.procesar_mensaje(alumno_id, contenido)
    
    if not resultado_moderacion['permitido']:
        # Mensaje bloqueado por moderación
        emit('mensaje_bloqueado', {
            'tipo': resultado_moderacion['tipo_accion'],
            'msg': resultado_moderacion['mensaje_sistema']
        })
        return
    
    # ✅ Mensaje aprobado, guardar en BD
    try:
        nuevo_mensaje = Mensaje(
            alumno_id=alumno_id,
            nombre_alumno=nombre,
            grado_grupo=grado_grupo,
            contenido=contenido
        )
        
        db.session.add(nuevo_mensaje)
        db.session.commit()
        
        # Emitir a todos en el grupo (incluyéndose a sí mismo)
        mensaje_data = {
            'id': nuevo_mensaje.id,
            'nombre': nombre,
            'texto': contenido,
            'hora': nuevo_mensaje.fecha.strftime('%H:%M'),
            'alumno_id': alumno_id
        }
        
        emit('nuevo_mensaje', mensaje_data, room=grado_grupo)
        
        log_info(f"💬 Mensaje de {nombre} ({grado_grupo}): {contenido[:50]}...")
        
    except Exception as e:
        log_error(f"Error al guardar mensaje: {str(e)}")
        emit('error', {'msg': 'Error al enviar mensaje'})


@socketio.on('usuario_escribiendo')
def handle_usuario_escribiendo(data):
    """Notificar cuando un usuario está escribiendo"""
    if 'alumno_id' not in session:
        return
    
    alumno_id = session.get('alumno_id')
    nombre = session.get('alumno_nombre')
    grado_grupo = session.get('alumno_grado')
    esta_escribiendo = data.get('escribiendo', False)
    
    if grado_grupo not in usuarios_escribiendo:
        usuarios_escribiendo[grado_grupo] = {}
    
    if esta_escribiendo:
        usuarios_escribiendo[grado_grupo][alumno_id] = nombre
    else:
        usuarios_escribiendo[grado_grupo].pop(alumno_id, None)
    
    # Notificar a otros (no incluirse a sí mismo)
    lista_escribiendo = [
        nombre for aid, nombre in usuarios_escribiendo[grado_grupo].items()
        if aid != alumno_id
    ]
    
    emit('usuarios_escribiendo', {
        'usuarios': lista_escribiendo
    }, room=grado_grupo, include_self=False)


@socketio.on('ping')
def handle_ping():
    """Mantener conexión viva"""
    emit('pong')