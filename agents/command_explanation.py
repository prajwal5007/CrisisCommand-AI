class CommandExplanationAgent:
    name = 'command_explanation'
    def explain(self, incident, selected, shortages=None):
        shortages = shortages or {}
        return {
            'incident_id': incident['id'],
            'summary': f"Plan for {incident['title']}: {len(selected)} resource(s) proposed.",
            'reason': 'Priority, proximity and current verified availability were considered; a human operator must approve high-impact actions.',
            'shortages': shortages,
            'approval_required': True,
        }
