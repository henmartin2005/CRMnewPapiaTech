"""Job diario: envía el recordatorio de pago 1 día antes del vencimiento de cada cuota.

PythonAnywhere → Tasks → Scheduled task (diaria, hora UTC; 13:00 UTC = 9:00 AM Miami en horario de verano):
    /home/henmartin2005/CRMnewPapiaTech/.venv/bin/python /home/henmartin2005/CRMnewPapiaTech/papia-crm/send_payment_reminders.py

Opciones:
    --days N     cuotas que vencen en N días (por defecto 1 = mañana)
    --dry-run    solo lista lo que enviaría, sin enviar
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402  (carga .env y registra blueprints/plantillas)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    with app.app_context():
        from datetime import datetime, timedelta
        from models.payment_schedule import due_for_reminder
        from services.payment_reminders import TZ, run_daily

        if args.dry_run:
            target = (datetime.now(TZ).date() + timedelta(days=args.days)).isoformat()
            rows = due_for_reminder(target)
            print(f"[dry-run] {len(rows)} cuota(s) vencen el {target}:")
            for r in rows:
                print(f"  #{r['id']} {r['label']} ${r['amount']:,.2f} → {r.get('email') or 'SIN EMAIL'}")
            return 0

        res = run_daily(days_ahead=args.days)
        print(f"Recordatorios para cuotas que vencen el {res['date']}")
        for s in res['sent']:
            print(f"  ✓ enviado: {s}")
        for s in res['skipped']:
            print(f"  - omitido: {s}")
        for s in res['errors']:
            print(f"  ✗ error: {s}")
        return 1 if res['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
