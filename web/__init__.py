# web/__init__.py
from flask import Flask, redirect, url_for, render_template, send_file, send_from_directory, flash
from .config import Config
from .extensions import db, cache  # ⬆️ CAMBIO: Agregar cache al import
from .routes.auth import auth_bp
from .routes.admin import admin_bp
from .routes.alumno import alumno_bp
from .models import Anuncio, Horario, Plataforma, Recurso, ActividadGrado, LibroDigital
from .utils import s3_manager, log_error
from io import BytesIO
import os
from flask import current_app

def create_app():
    # 1. Creamos la instancia de Flask
    app = Flask(__name__)
    
    # 2. Cargamos la configuración
    app.config.from_object(Config)

    # 3. Inicializamos la base de datos
    db.init_app(app)
    
    # ⬆️ CAMBIO: Inicializar caché
    cache.init_app(app)

    # 4. Registramos los Blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(alumno_bp, url_prefix='/alumnos')

    # 5. RUTA PRINCIPAL - INDEX con datos
    @app.route('/')
    @cache.cached(timeout=60, key_prefix='index_page')  # ⬆️ CAMBIO: Agregar decorador de caché
    def index():
        """Página principal del sitio"""
        anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(5).all()
        horarios = Horario.query.all()
        plataformas = Plataforma.query.all()
        recursos = Recurso.query.order_by(Recurso.fecha.desc()).limit(10).all()  # ⬆️ CAMBIO: Agregar LIMIT 10
        
        # 📚 NUEVO: Biblioteca
        libros_biblioteca = LibroDigital.query.filter_by(activo=True)\
            .order_by(LibroDigital.fecha_publicacion.desc())\
            .limit(12)\ 
            .all()

        return render_template('index.html', 
                             anuncios=anuncios, 
                             horarios=horarios, 
                             plataformas=plataformas, 
                             recursos=recursos,
                             libros_biblioteca=libros_biblioteca)  # 📚 NUEVO
    
    # 6. RUTA PARA VER GRADOS
    @app.route('/grado/<int:numero_grado>')
    @cache.cached(timeout=300, key_prefix='grado_%s')  # ⬆️ CAMBIO: Agregar decorador de caché
    def ver_grado(numero_grado):
        """Ver actividades de un grado específico"""
        actividad = ActividadGrado.query.filter_by(grado=numero_grado).first()
        return render_template('publico/ver_grado.html', grado=numero_grado, actividad=actividad)
    
    # 7. 🆕 RUTA PARA VER ARCHIVOS CON URLs FIRMADAS (iDrive e2)
    @app.route('/ver-archivo/<path:archivo_path>')
    @cache.cached(timeout=3600, key_prefix='archivo_%s')  # ⬆️ CAMBIO: Agregar decorador de caché
    def ver_archivo(archivo_path):
        """Permite ver/descargar archivos con URLs firmadas para iDrive e2"""
        try:
            # Si S3 está configurado (iDrive e2)
            if s3_manager.is_configured:
                try:
                    # Generar URL firmada válida por 1 hora
                    url_firmada = s3_manager.generate_presigned_url(archivo_path, expiration=3600)
                    
                    # Redirigir a la URL firmada
                    return redirect(url_firmada)
                    
                except Exception as e:
                    log_error(f"Error al generar URL firmada: {str(e)}")
                    flash('Error al acceder al archivo', 'danger')
                    return redirect(url_for('index'))
            else:
                # Fallback: archivos locales
                if archivo_path.startswith('uploads/'):
                    archivo_path = archivo_path.replace('uploads/', '')
                
                file_path = os.path.join('uploads', archivo_path)
                
                if not os.path.exists(file_path):
                    return "Archivo no encontrado", 404
                
                # Determinar mimetype
                ext = archivo_path.split('.')[-1].lower()
                mimetype_map = {
                    'pdf': 'application/pdf',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'png': 'image/png',
                    'gif': 'image/gif',
                    'webp': 'image/webp',
                    'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                }
                mimetype = mimetype_map.get(ext, 'application/octet-stream')
                
                return send_file(
                    file_path,
                    mimetype=mimetype,
                    as_attachment=False,
                    download_name=os.path.basename(file_path)
                )
                        
        except Exception as e:
            log_error(f"Error al servir archivo: {str(e)}")
            return f"Error al cargar archivo: {str(e)}", 500

    # 8. 📖 RUTA PARA VER UN LIBRO (incrementa contador de vistas)
    @app.route('/biblioteca/<int:libro_id>/ver')
    def ver_libro(libro_id):
        """Abre el PDF del libro y cuenta como vista"""
        libro = LibroDigital.query.get_or_404(libro_id)
        
        if not libro.activo:
            flash('Este libro no está disponible', 'warning')
            return redirect(url_for('index'))
        
        # Incrementar vistas
        libro.incrementar_vistas()
        
        # Redirigir a la URL firmada del PDF
        return redirect(url_for('ver_archivo', archivo_path=libro.archivo_pdf_url))

    # 9. 📥 RUTA PARA DESCARGAR UN LIBRO (incrementa contador de descargas)
    @app.route('/biblioteca/<int:libro_id>/descargar')
    def descargar_libro(libro_id):
        """Descarga el PDF del libro y cuenta como descarga"""
        libro = LibroDigital.query.get_or_404(libro_id)
        
        if not libro.activo:
            flash('Este libro no está disponible', 'warning')
            return redirect(url_for('index'))
        
        # Incrementar descargas
        libro.incrementar_descargas()
        
        # Descargar el PDF
        try:
            if s3_manager.is_configured:
                # Generar URL firmada para descarga
                url_firmada = s3_manager.generate_presigned_url(
                    libro.archivo_pdf_url, 
                    expiration=3600  # 1 hora
                )
                
                # Redirigir a la URL firmada (descarga automática)
                return redirect(url_firmada)
                
            else:
                # Fallback: archivo local
                ruta_local = os.path.join(
                    current_app.config.get('UPLOAD_FOLDER', 'uploads'), 
                    libro.archivo_pdf_url
                )
                
                if not os.path.exists(ruta_local):
                    flash('Archivo no encontrado', 'danger')
                    return redirect(url_for('index'))
                
                return send_file(
                    ruta_local,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=f"{libro.titulo}.pdf"
                )
                
        except Exception as e:
            log_error(f"Error al descargar libro: {str(e)}")
            flash(f'Error al descargar: {str(e)}', 'danger')
            return redirect(url_for('index'))

    # 10. Crear tablas si no existen
    with app.app_context():
        db.create_all()

    return app