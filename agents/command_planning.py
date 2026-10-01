class CommandPlanningAgent:
    name = 'command_planning'

    def build(self, incident, selected, route_logistics, shortages=None):
        shortages = shortages or {}
        return {
            'incident_id': incident['id'],
            'summary': f"Coordinated plan for {incident['title']}: {len(selected)} resource(s) proposed.",
            'reason': (
                'Priority, current verified availability and simulated response logistics '
                'were considered. Critical or conflicting actions require human approval.'
            ),
            'shortages': shortages,
            'route_logistics': route_logistics,
            'human_approval_required': (
                incident.get('severity') == 'CRITICAL'
                or bool(shortages)
                or bool(incident.get('source_confidence') != 'VERIFIED')
            )
        }
