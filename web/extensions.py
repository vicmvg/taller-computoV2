from flask_sqlalchemy import SQLAlchemy

# Creamos la instancia de SQLAlchemy de forma aislada.
# Más adelante, la "uniremos" a la aplicación en el archivo de inicio.
db = SQLAlchemy()