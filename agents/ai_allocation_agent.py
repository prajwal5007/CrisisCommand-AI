"""Explainable AI resource allocation scorer.

This is intentionally transparent and deterministic for a hackathon/demo:
it scores feasible resource candidates using incident priority, response distance,
resource scarcity and provider load. It is decision support; a human approves
critical/conflicting allocations.
"""
from agents.incident_agent import WEIGHT
from services.geo_service import haversine_km


class AIResourceAllocationAgent:
    name = 'ai_resource_allocation'

    def _incident_priority(self, incident):
        severity = WEIGHT.get(str(incident.get('severity', 'MEDIUM')).upper(), 2)
        urgency = max(0, min(int(incident.get('urgency') or 0), 10)) / 10
        affected = min(int(incident.get('affected_people') or 0), 50) / 50
        return severity / 4 * 0.45 + urgency * 0.35 + affected * 0.20

    def score_resource(self, incident, resource, provider_load=0, total_available_of_type=1):
        if resource.get('state') != 'AVAILABLE':
            return -1, ['resource is not currently available']
        lat = incident.get('latitude')
        lon = incident.get('longitude')
        rlat = resource.get('latitude') if resource.get('latitude') is not None else resource.get('hospital_latitude')
        rlon = resource.get('longitude') if resource.get('longitude') is not None else resource.get('hospital_longitude')
        distance = haversine_km(float(lat), float(lon), float(rlat), float(rlon)) if None not in (lat, lon, rlat, rlon) else 999.0
        proximity = max(0.0, 1.0 - min(distance, 50.0) / 50.0)
        scarcity = 1.0 / max(1, int(total_available_of_type))
        provider_penalty = min(provider_load / 10.0, 1.0)
        priority = self._incident_priority(incident)
        score = priority * 0.45 + proximity * 0.30 + scarcity * 0.15 + (1 - provider_penalty) * 0.10
        reasons = [
            f"incident priority={priority:.2f}",
            f"distance={distance:.1f} km",
            f"resource scarcity factor={scarcity:.2f}",
            f"provider load={provider_load}",
        ]
        return round(score, 4), reasons

    def rank(self, incident, candidates, provider_loads=None):
        provider_loads = provider_loads or {}
        by_type = {}
        for r in candidates:
            by_type[r['resource_type']] = by_type.get(r['resource_type'], 0) + 1
        ranked = []
        for r in candidates:
            score, reasons = self.score_resource(
                incident, r,
                provider_loads.get(r.get('hospital_id'), 0),
                by_type.get(r['resource_type'], 1),
            )
            ranked.append({**r, 'ai_score': score, 'ai_reasons': reasons})
        ranked.sort(key=lambda x: (x['ai_score'], -int(x.get('id', 0))), reverse=True)
        return ranked
