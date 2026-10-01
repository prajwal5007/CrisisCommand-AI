from database.db import query


def audit(actor_id, action, entity_type=None, entity_id=None, details=''):
    return query(
        '''INSERT INTO audit_logs (actor_id, action, entity_type, entity_id, details_json)
           VALUES (%s,%s,%s,%s,%s)''',
        (actor_id, action, entity_type, entity_id, details),
    )
