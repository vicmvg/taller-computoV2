# web/__init__.py
from flask import Flask, redirect, url_for, render_template, send_file, send_from_directory, flash
from .config import Config
from .extensions import db
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

    # 4. Registramos los Blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(alumno_bp, url_prefix='/alumnos')

    # 5. RUTA PRINCIPAL - INDEX con datos
    @app.route('/')
    def index():
        """Página principal del sitio"""
        anuncios = Anuncio.query.order_by(Anuncio.fecha.desc()).limit(5).all()
        horarios = Horario.query.all()
        plataformas = Plataforma.query.all()
        recursos = Recurso.query.order_by(Recurso.fecha.desc()).all()
        
        # 📚 NUEVO: Biblioteca
        libros_biblioteca = LibroDigital.query.filter_by(activo=True)\
            .order_by(LibroDigital.fecha_publicacion.desc())\
            .all()

        return render_template('index.html', 
                             anuncios=anuncios, 
                             horarios=horarios, 
                             plataformas=plataformas, 
                             recursos=recursos,
                             libros_biblioteca=libros_biblioteca)  # 📚 NUEVO
    
    # 6. RUTA PARA VER GRADOS
    @app.route('/grado/<int:numero_grado>')
    def ver_grado(numero_grado):
        """Ver actividades de un grado específico"""
        actividad = ActividadGrado.query.filter_by(grado=numero_grado).first()
        return render_template('publico/ver_grado.html', grado=numero_grado, actividad=actividad)
    
    # 7. RUTA PARA VER ARCHIVOS PÚBLICOS (recursos del index)
    @app.route('/ver-archivo/<path:archivo_path>')
    def ver_archivo(archivo_path):
        """Permite ver/descargar archivos públicos (recursos)"""
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
                    # Archivo local
                    filename = archivo_path.replace('uploads/', '')
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
                # Archivo local sin prefijo uploads/
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
                    return "Archivo no encontrado", 404
                    
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
        
        # Redirigir al PDF
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
                # Descargar de S3
                file_stream, content_type = s3_manager.download_file(libro.archivo_pdf_url)
                if file_stream:
                    return send_file(
                        file_stream,
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f"{libro.titulo}.pdf"
                    )
                else:
                    flash('Error al obtener el archivo desde S3', 'danger')
                    return redirect(url_for('index'))
            else:
                # Descargar local
                ruta_local = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), libro.archivo_pdf_url)
                return send_file(
                    ruta_local,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=f"{libro.titulo}.pdf"
                )
        except Exception as e:
            flash(f'Error al descargar: {str(e)}', 'danger')
            return redirect(url_for('index'))

    # 10. Crear tablas si no existen
    with app.app_context():
        db.create_all()

    return app