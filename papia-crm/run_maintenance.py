from maintenance import ensure_schema, tick
if __name__ == '__main__':
    ensure_schema()
    tick()
    print('Papia: mantenimiento actualizado; avisos internos, sin envíos ni suspensión automática.')
