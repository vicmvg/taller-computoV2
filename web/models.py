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