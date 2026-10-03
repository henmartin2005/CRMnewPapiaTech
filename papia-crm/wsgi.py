"""
Punto de entrada WSGI.

En PythonAnywhere, el archivo WSGI de la pestaña Web (/var/www/<dominio>_wsgi.py)
solo necesita esto (cambia la ruta):

    import sys
    path = '/home/<usuario>/<carpeta-del-repo>/papia-crm'
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application

app.py ya carga el .env de esta carpeta.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import app as application  # noqa: E402,F401
