"""Job diario: correos festivos (Navidad, Año Nuevo, San Valentín, 4 de Julio, Thanksgiving).

- 3 días antes: crea la lista de envíos 'pendiente' con los clientes marcados y avisa al admin.
- El día del envío: manda los que siguen 'pendiente'.

PythonAnywhere → Tasks → Scheduled task (diaria, hora UTC; 13:00 UTC = 8:00 AM Miami en invierno,
9:00 AM en verano):
    /home/henmartin2005/CRMnewPapiaTech/.venv/bin/python /home/henmartin2005/CRMnewPapiaTech/papia-crm/send_festivos.py

Opciones:
    --dry-run          solo muestra qué haría, sin crear ni enviar nada
    --fecha AAAA-MM-DD simula otro día (útil con --dry-run)
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402  (carga .env, crea las tablas y registra plantillas)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--fecha')
    args = parser.parse_args()

    with app.app_context():
        from services.festivos import run_daily, today_local
        today = date.fromisoformat(args.fecha) if args.fecha else today_local()
        report = run_daily(today=today, dry_run=args.dry_run)
        print(f"Correos festivos · {today.isoformat()}{' (dry-run)' if args.dry_run else ''}")
        for line in report or ['  (nada que hacer hoy)']:
            print(line)
        return 1 if any('✗' in line for line in report) else 0


if __name__ == '__main__':
    sys.exit(main())
