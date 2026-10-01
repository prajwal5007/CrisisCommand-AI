from services.geo_service import haversine_km
from database.db import query

class HospitalCoordinationAgent:
    name = 'nearby_hospital_coordination'

    def find_nearby(self, latitude, longitude, requirements, initial_radius_km=10, max_radius_km=100, step_km=10):
        hospitals = query(
            '''SELECT id, name, verified, latitude, longitude
               FROM organizations
               WHERE organization_type='HOSPITAL' AND verified=TRUE
                 AND latitude IS NOT NULL AND longitude IS NOT NULL''',
            fetch=True
        )
        # Hospital discovery is intentionally based on VERIFIED + PROXIMITY,
        # not on current AVAILABLE resources. A hospital may have no currently
        # free unit in the database but can still receive the request, review its
        # live inventory, and respond with ACCEPT / PARTIAL / REJECT.
        resources = query(
            '''SELECT r.id, r.hospital_id, r.resource_type, r.state, r.label
               FROM resources r JOIN organizations o ON o.id=r.hospital_id
               WHERE o.organization_type='HOSPITAL' AND o.verified=TRUE''', fetch=True)
        by_hospital = {}
        for r in resources:
            by_hospital.setdefault(r['hospital_id'], []).append(r)

        radius = initial_radius_km
        selected = []
        while radius <= max_radius_km and not selected:
            eligible = []
            for h in hospitals:
                distance = haversine_km(latitude, longitude, h['latitude'], h['longitude'])
                if distance <= radius:
                    counts = {}
                    states = {}
                    for r in by_hospital.get(h['id'], []):
                        counts[r['resource_type']] = counts.get(r['resource_type'], 0) + 1
                        if r['state'] == 'AVAILABLE':
                            states[r['resource_type']] = states.get(r['resource_type'], 0) + 1
                    eligible.append({
                        **h,
                        'distance_km': round(distance, 2),
                        'available': states,
                        'registered': counts,
                    })
            eligible.sort(key=lambda x: x['distance_km'])
            selected = eligible
            if selected:
                break
            radius += step_km
        return {'radius_km': min(radius, max_radius_km), 'hospitals': selected, 'expanded': radius > initial_radius_km}
