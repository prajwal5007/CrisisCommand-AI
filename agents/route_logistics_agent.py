from services.geo_service import haversine_km


class RouteLogisticsAgent:
    """Provides transparent, simulated route/ETA information for dispatch planning."""

    name = 'route_logistics'

    def plan(self, incident, resources):
        rows = []
        for resource in resources:
            rlat = resource.get('latitude') or resource.get('hospital_latitude')
            rlon = resource.get('longitude') or resource.get('hospital_longitude')
            if rlat is None or rlon is None:
                continue
            distance = haversine_km(incident['latitude'], incident['longitude'], rlat, rlon)
            # Demo estimate: road-distance factor over straight-line distance,
            # then a conservative city-response average speed.
            road_km = distance * 1.25
            eta_min = max(2, round((road_km / 30.0) * 60, 1))
            rows.append({
                'resource_id': resource.get('id'),
                'resource_label': resource.get('label'),
                'hospital_id': resource.get('hospital_id'),
                'hospital_name': resource.get('hospital_name'),
                'resource_type': resource.get('resource_type'),
                'distance_km': round(distance, 2),
                'estimated_road_km': round(road_km, 2),
                'estimated_eta_minutes': eta_min,
                'eta_basis': 'simulated; Haversine distance × 1.25 road factor at 30 km/h average'
            })
        rows.sort(key=lambda x: (x['estimated_eta_minutes'], x['distance_km']))
        return {
            'incident_id': incident.get('id'),
            'routes': rows,
            'simulation': True
        }
