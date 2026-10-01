from database.db import query
from agents.ai_allocation_agent import AIResourceAllocationAgent


class ResourceAllocationAgent:
    name = "resource_allocation"

    def __init__(self):
        self.ai = AIResourceAllocationAgent()

    def propose(self, incident, requirements, hospital_ids=None, eligible_resource_ids=None):
        selected = []
        ai_scores = []
        used = set()
        provider_loads = {}
        for resource_type, count in requirements.items():
            params = [resource_type]
            extra = ""
            if hospital_ids:
                placeholders = ",".join(["%s"] * len(hospital_ids))
                extra += f" AND r.hospital_id IN ({placeholders})"
                params.extend(hospital_ids)
            if eligible_resource_ids is not None:
                if not eligible_resource_ids:
                    return {
                        'ok': False,
                        'shortages': {resource_type: int(count)},
                        'selected': [],
                        'ai_method': 'Explainable weighted resource scoring',
                        'ai_reason': 'No hospital has confirmed allocatable resources for this request yet.'
                    }
                placeholders = ",".join(["%s"] * len(eligible_resource_ids))
                extra += f" AND r.id IN ({placeholders})"
                params.extend(eligible_resource_ids)
            rows = query(
                f"""SELECT r.*, o.name AS hospital_name, o.latitude AS hospital_latitude,
                           o.longitude AS hospital_longitude
                    FROM resources r JOIN organizations o ON o.id=r.hospital_id
                    WHERE r.state='AVAILABLE' AND o.verified=TRUE AND r.resource_type=%s {extra}
                    ORDER BY r.id""", tuple(params), True)
            rows = [r for r in rows if r['id'] not in used]
            ranked = self.ai.rank(incident, rows, provider_loads)
            if len(ranked) < int(count):
                return {
                    'ok': False,
                    'shortages': {resource_type: int(count) - len(ranked)},
                    'selected': [],
                    'ai_method': 'Explainable weighted resource scoring',
                    'ai_reason': 'The AI allocation engine could not find enough currently available verified resources.'
                }
            chosen = ranked[:int(count)]
            for r in chosen:
                used.add(r['id'])
                provider_loads[r.get('hospital_id')] = provider_loads.get(r.get('hospital_id'), 0) + 1
                ai_scores.append({
                    'resource_id': r['id'],
                    'score': r['ai_score'],
                    'reasons': r['ai_reasons']
                })
            selected.extend(chosen)
        return {
            'ok': True,
            'selected': selected,
            'ai_method': 'Explainable weighted resource scoring',
            'ai_scores': ai_scores,
            'human_approval_required': incident['severity'] == 'CRITICAL' or bool(incident.get('source_confidence') != 'VERIFIED'),
            'reason': 'AI ranked feasible resources using incident priority, urgency, proximity, scarcity and provider load. A human must approve critical or conflicting actions.'
        }
