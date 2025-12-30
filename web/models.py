# web/models.py
from datetime import datetime
from .extensions import db

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
    
    # RELACIONES CON back_populates
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
    """Modelo mejorado para actividades semanales por grado"""
    id = db.Column(db.Integer, primary_key=True)
    grado = db.Column(db.Integer, nullable=False)
    
    # Información básica
    titulo = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text)
    
    # Información de la semana
    numero_semana = db.Column(db.Integer)
    fecha_inicio = db.Column(db.Date, nullable=True)
    fecha_fin = db.Column(db.Date, nullable=True)
    
    # Objetivos y contenido
    objetivos = db.Column(db.Text)
    material_necesario = db.Column(db.Text)
    tareas = db.Column(db.Text)
    observaciones = db.Column(db.Text)
    
    # Metadatos
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    actualizado_por = db.Column(db.String(100))

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

class Encuesta(db.Model):
    """Encuestas de retroalimentación enviadas por el profesor"""
    __tablename__ = 'encuestas'
    
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=True)
    
    # A quién va dirigida
    grupos_destino = db.Column(db.String(200), nullable=False)  # "todos" o "1A,2B,3A"
    
    # Estado
    activa = db.Column(db.Boolean, default=True)
    obligatoria = db.Column(db.Boolean, default=True)  # Si bloquea el acceso
    
    # Metadatos
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_cierre = db.Column(db.DateTime, nullable=True)  # Opcional: fecha límite
    creado_por = db.Column(db.String(100), nullable=False)
    
    # Relaciones
    respuestas = db.relationship('RespuestaEncuesta', backref='encuesta', lazy=True, cascade='all, delete-orphan')
    
    def grupos_lista(self):
        """Retorna lista de grupos destino"""
        if self.grupos_destino == 'todos':
            return ['todos']
        return [g.strip() for g in self.grupos_destino.split(',') if g.strip()]
    
    def aplica_para_grupo(self, grado_grupo):
        """Verifica si la encuesta aplica para un grupo específico"""
        if self.grupos_destino == 'todos':
            return True
        return grado_grupo in self.grupos_lista()
    
    def total_respuestas(self):
        """Cuenta total de respuestas recibidas"""
        return RespuestaEncuesta.query.filter_by(encuesta_id=self.id).count()
    
    def alumno_ya_respondio(self, alumno_id):
        """Verifica si un alumno ya respondió esta encuesta"""
        return RespuestaEncuesta.query.filter_by(
            encuesta_id=self.id,
            alumno_id=alumno_id
        ).first() is not None


class RespuestaEncuesta(db.Model):
    """Respuestas individuales de alumnos a las encuestas"""
    __tablename__ = 'respuestas_encuesta'
    
    id = db.Column(db.Integer, primary_key=True)
    encuesta_id = db.Column(db.Integer, db.ForeignKey('encuestas.id', ondelete='CASCADE'), nullable=False)
    alumno_id = db.Column(db.Integer, db.ForeignKey('usuario_alumno.id', ondelete='CASCADE'), nullable=False)
    
    # Información del alumno (para facilitar consultas)
    nombre_alumno = db.Column(db.String(100), nullable=False)
    grado_grupo = db.Column(db.String(20), nullable=False)
    
    # Respuestas a preguntas predefinidas (escala 1-5)
    pregunta1_clases = db.Column(db.Integer, nullable=False)  # ¿Te gustan las clases?
    pregunta2_aprendizaje = db.Column(db.Integer, nullable=False)  # ¿Sientes que aprendes?
    pregunta3_maestro = db.Column(db.Integer, nullable=False)  # ¿Cómo calificarías al maestro?
    pregunta4_contenido = db.Column(db.Integer, nullable=False)  # ¿El contenido es interesante?
    pregunta5_dificultad = db.Column(db.Integer, nullable=False)  # ¿La dificultad es adecuada?
    
    # Comentarios adicionales
    comentario_positivo = db.Column(db.Text, nullable=True)  # ¿Qué te gusta más?
    comentario_mejora = db.Column(db.Text, nullable=True)  # ¿Qué mejorarías?
    comentario_adicional = db.Column(db.Text, nullable=True)  # Otros comentarios
    
    # Metadatos
    fecha_respuesta = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relaciones
    alumno = db.relationship('UsuarioAlumno', backref='respuestas_encuestas')
    
    def promedio_respuestas(self):
        """Calcula el promedio de las respuestas numéricas"""
        respuestas = [
            self.pregunta1_clases,
            self.pregunta2_aprendizaje,
            self.pregunta3_maestro,
            self.pregunta4_contenido,
            self.pregunta5_dificultad
        ]
        return sum(respuestas) / len(respuestas)

class LibroDigital(db.Model):
    """Libros y documentos de la biblioteca digital"""
    __tablename__ = 'libros_digitales'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Información básica
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    autor = db.Column(db.String(100), nullable=True)
    
    # Categoría
    categoria = db.Column(db.String(50), nullable=False)
    
    # Archivos
    archivo_pdf_url = db.Column(db.String(500), nullable=False)
    miniatura_url = db.Column(db.String(500), nullable=True)
    
    # Metadatos
    fecha_publicacion = db.Column(db.DateTime, default=datetime.utcnow)
    publicado_por = db.Column(db.String(100), nullable=False)
    vistas = db.Column(db.Integer, default=0)
    descargas = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    
    def incrementar_vistas(self):
        """Incrementa el contador de vistas"""
        self.vistas += 1
        db.session.commit()
    
    def incrementar_descargas(self):
        """Incrementa el contador de descargas"""
        self.descargas += 1
        db.session.commit()

class ReporteClase(db.Model):
    """Reportes detallados de las clases impartidas"""
    __tablename__ = 'reportes_clase'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Información básica
    fecha_clase = db.Column(db.Date, nullable=False)
    hora_inicio = db.Column(db.String(10), nullable=False)
    hora_fin = db.Column(db.String(10), nullable=False)
    grado_grupo = db.Column(db.String(20), nullable=False)
    
    # Contenido de la clase
    tema = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    objetivos_cumplidos = db.Column(db.Text, nullable=True)
    
    # Observaciones e incidencias
    incidencias = db.Column(db.Text, nullable=True)
    observaciones = db.Column(db.Text, nullable=True)
    
    # Información de asistencia (opcional)
    total_alumnos = db.Column(db.Integer, nullable=True)
    alumnos_presentes = db.Column(db.Integer, nullable=True)
    alumnos_ausentes = db.Column(db.Integer, nullable=True)
    
    # Datos del maestro
    maestro_computo = db.Column(db.String(100), nullable=False)
    maestro_grupo = db.Column(db.String(100), nullable=True)
    
    # Metadatos
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_modificacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    creado_por = db.Column(db.String(100), nullable=False)
    
    def __repr__(self):
        return f'<ReporteClase {self.grado_grupo} - {self.fecha_clase} - {self.tema}>'
    
    @property
    def porcentaje_asistencia(self):
        """Calcula el porcentaje de asistencia si hay datos"""
        if self.total_alumnos and self.alumnos_presentes:
            return round((self.alumnos_presentes / self.total_alumnos) * 100, 1)
        return None