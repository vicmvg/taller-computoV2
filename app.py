# app.py
from dotenv import load_dotenv
import os

# Cargar variables de entorno desde .env
load_dotenv()

from web import create_app

# Creamos la aplicación usando la fábrica
app = create_app()

if __name__ == '__main__':
    # Flask buscará automáticamente las variables de entorno o usará los valores por defecto
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=port)