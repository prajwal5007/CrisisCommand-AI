from database.db import get_connection, query

VALID_TYPES = {'AMBULANCE', 'MEDICAL_TEAM', 'RESCUE_TEAM', 'SHELTER'}
VALID_STATES = {'AVAILABLE', 'RESERVED', 'DISPATCHED', 'UNAVAILABLE', 'MAINTENANCE'}


def available_resources(resource_type=None, hospital_id=None):
    clauses = ["r.state='AVAILABLE'"]
    params = []
    if resource_type:
        clauses.append('r.resource_type=%s')
        params.append(resource_type)
    if hospital_id:
        clauses.append('r.hospital_id=%s')
        params.append(hospital_id)
    return query(
        f'''SELECT r.*, o.name AS hospital_name, o.verified AS hospital_verified,
                   o.latitude AS hospital_latitude, o.longitude AS hospital_longitude
            FROM resources r JOIN organizations o ON o.id=r.hospital_id
            WHERE {' AND '.join(clauses)} ORDER BY r.id''',
        tuple(params), True
    )


def set_resource_state(resource_id, state, hospital_id=None):
    if state not in VALID_STATES:
        raise ValueError('Invalid resource state')
    conn = get_connection()
    cur = conn.cursor()
    try:
        if hospital_id:
            cur.execute('UPDATE resources SET state=%s WHERE id=%s AND hospital_id=%s', (state, resource_id, hospital_id))
        else:
            cur.execute('UPDATE resources SET state=%s WHERE id=%s', (state, resource_id))
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()
