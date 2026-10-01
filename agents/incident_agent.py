WEIGHT = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'CRITICAL': 4}


class IncidentAssessmentAgent:
    name = 'incident_assessment'


    def infer_severity_urgency(self, incident):
        text = ' '.join(str(incident.get(k) or '') for k in ('title', 'description', 'additional_notes')).lower()
        affected = max(0, int(incident.get('affected_people') or 0))
        critical_signal = any(k in text for k in ('collapse', 'collapsed', 'explosion', 'trapped', 'drowning', 'unconscious', 'not breathing', 'fire'))
        high_signal = any(k in text for k in ('accident', 'collision', 'crash', 'injured', 'bleeding', 'flood', 'rescue', 'industrial'))
        if critical_signal or affected >= 20:
            return 'CRITICAL', 9
        if high_signal or affected >= 8:
            return 'HIGH', 7
        if affected > 0:
            return 'MEDIUM', 5
        return 'MEDIUM', 4

    def infer_resource_needs(self, incident):
        """Deterministic, explainable first-pass resource estimation for public reports."""
        text = ' '.join(str(incident.get(k) or '') for k in ('title', 'description', 'additional_notes')).lower()
        affected = max(0, int(incident.get('affected_people') or 0))
        severity = str(incident.get('severity') or 'MEDIUM').upper()
        needs = {}

        accident = any(k in text for k in ('accident', 'collision', 'crash', 'vehicle', 'road', 'highway', 'junction', 'bus', 'truck', 'bike', 'car'))
        fire = any(k in text for k in ('fire', 'smoke', 'burn', 'explosion'))
        collapse = any(k in text for k in ('collapse', 'collapsed', 'building', 'debris', 'rubble', 'trapped'))
        flood = any(k in text for k in ('flood', 'flooding', 'waterlogging', 'drowning'))
        medical = any(k in text for k in ('medical', 'unconscious', 'injury', 'injured', 'bleeding', 'heart', 'breathing', 'sick'))
        rescue = any(k in text for k in ('trapped', 'rescue', 'collapse', 'debris', 'fire', 'explosion', 'flood', 'drowning'))

        if affected > 0:
            needs['AMBULANCE'] = max(1, (affected + 1) // 2)  # Demo rule: maximum 2 affected people per ambulance
        elif accident or medical or fire or collapse:
            needs['AMBULANCE'] = 1

        if medical or accident or fire or collapse or flood or severity in {'HIGH', 'CRITICAL'}:
            needs['MEDICAL_TEAM'] = max(1, min(8, (max(affected, 1) + 9) // 10))

        if rescue:
            needs['RESCUE_TEAM'] = max(1, min(8, (max(affected, 1) + 9) // 10))

        if severity == 'CRITICAL' and any((fire, collapse, flood)):
            needs['RESCUE_TEAM'] = max(1, needs.get('RESCUE_TEAM', 0))

        return needs

    def assess(self, incident):
        severity = str(incident.get('severity') or '').upper()
        urgency_value = incident.get('urgency')
        if severity not in WEIGHT or incident.get('auto_assess'):
            severity, inferred_urgency = self.infer_severity_urgency(incident)
            urgency = inferred_urgency
        else:
            urgency = max(0, min(int(urgency_value or 0), 10))
        affected = int(incident.get('affected_people') or 0)
        score = WEIGHT[severity] * 2 + urgency + (3 if affected >= 15 else 0)
        needs = incident.get('resource_needs') or self.infer_resource_needs(incident)
        return {
            'severity': severity,
            'urgency': urgency,
            'affected_people': affected,
            'priority_score': score,
            'resource_needs': needs,
            'confidence': 'HIGH' if incident.get('source') in {'USER', 'PUBLIC'} and incident.get('latitude') is not None else 'MEDIUM',
            'human_review': severity == 'CRITICAL' or incident.get('source_confidence') != 'VERIFIED',
            'reason': f'{severity} severity, urgency {urgency}/10, {affected} affected; human review remains required for high-impact actions.'
        }
