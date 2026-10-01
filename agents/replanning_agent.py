from collections import defaultdict

from agents.conflict_agent import ConflictDetectionAgent
from agents.aggregation_agent import ResourceAggregationAgent
from agents.route_logistics_agent import RouteLogisticsAgent
from agents.ai_allocation_agent import AIResourceAllocationAgent


class DynamicReplanningAgent:
    name = 'dynamic_replanning'

    def __init__(self):
        self.conflict = ConflictDetectionAgent()
        self.aggregation = ResourceAggregationAgent()
        self.route = RouteLogisticsAgent()
        self.ai = AIResourceAllocationAgent()

    def replan(self, incidents, resources, previous_allocations=None, eligible_by_incident=None):
        """Create a revised plan using AI scoring and controlled reallocation candidates.

        AVAILABLE resources can be selected normally. RESERVED resources belonging to a
        lower-priority incident may be proposed for reassignment to a higher-priority
        incident, but are never silently changed; such changes are flagged for human approval.
        DISPATCHED/UNAVAILABLE/MAINTENANCE resources are never reallocated.
        """
        previous_allocations = previous_allocations or []
        eligible_by_incident = eligible_by_incident or {}
        incident_by_id = {i['id']: i for i in incidents}
        priority = {i['id']: i['priority_score'] for i in incidents}
        owner_by_resource = {}
        for a in previous_allocations:
            if a.get('status') in {'PROPOSED', 'RESERVED'}:
                owner_by_resource[a['resource_id']] = a['incident_id']

        pool = []
        for idx, resource in enumerate(resources):
            item = dict(resource)
            item.setdefault('id', -(idx + 1))
            state = item.get('state')
            if state == 'AVAILABLE':
                item['reallocation_candidate'] = False
                pool.append(item)
            elif state == 'RESERVED':
                owner = owner_by_resource.get(item['id'])
                item['owner_incident_id'] = owner
                item['reallocation_candidate'] = bool(owner and priority.get(owner, 0) < priority.get(item.get('_current_incident_id'), 0))
                pool.append(item)

        plans = []
        reserved_ids = set()
        previous_by_incident = defaultdict(list)
        for a in previous_allocations:
            previous_by_incident[a['incident_id']].append(a)

        for incident in sorted(incidents, key=lambda x: x['priority_score'], reverse=True):
            needs = incident.get('resource_needs') or {}
            selected = []
            selected_by_type = defaultdict(list)
            reallocation_candidates = []
            shortages = {}
            provider_loads = defaultdict(int)

            for resource_type, count in needs.items():
                allowed_ids = eligible_by_incident.get(incident['id'])
                candidates = [
                    r for r in pool
                    if r['resource_type'] == resource_type
                    and allowed_ids is not None
                    and r['id'] in allowed_ids
                    and r['id'] not in reserved_ids
                    and (
                        r.get('state') == 'AVAILABLE'
                        or (
                            r.get('state') == 'RESERVED'
                            and (
                                r.get('owner_incident_id') == incident['id']
                                or (
                                    r.get('owner_incident_id') != incident['id']
                                    and priority.get(r.get('owner_incident_id'), 0) < incident['priority_score']
                                    and incident['severity'] in {'HIGH', 'CRITICAL'}
                                )
                            )
                        )
                    )
                ]
                ranked = self.ai.rank(incident, candidates, provider_loads)
                chosen = ranked[:int(count)]
                if len(chosen) < int(count):
                    shortages[resource_type] = int(count) - len(chosen)
                for r in chosen:
                    r['_ai_selected_for'] = incident['id']
                    selected.append(r)
                    selected_by_type[resource_type].append(r)
                    reserved_ids.add(r['id'])
                    provider_loads[r.get('hospital_id')] += 1
                    if r.get('state') == 'RESERVED':
                        reallocation_candidates.append({
                            'resource_id': r['id'],
                            'from_incident_id': r.get('owner_incident_id'),
                            'to_incident_id': incident['id'],
                            'reason': 'Higher-priority incident requires a resource currently reserved to a lower-priority incident.'
                        })

            feasible = not shortages
            previous_ids = {a['resource_id'] for a in previous_by_incident.get(incident['id'], []) if a.get('resource_id')}
            new_ids = {r['id'] for r in selected}
            added = sorted(new_ids - previous_ids)
            removed = sorted(previous_ids - new_ids)

            reason_parts = []
            if shortages:
                reason_parts.append('The AI engine could not satisfy the full requirement from the latest usable pool.')
                reason_parts.append('The remaining shortage is flagged for human command attention.')
            else:
                reason_parts.append('AI ranked resources using priority, urgency, proximity, scarcity and provider load.')
            if reallocation_candidates:
                reason_parts.append('A higher-priority incident required a proposed reassignment from a lower-priority reservation; human approval is mandatory.')
            if added:
                reason_parts.append(f'Added/replaced resource IDs: {added}.')
            if removed:
                reason_parts.append(f'Removed resource IDs: {removed}.')

            routes = self.route.plan(incident, selected) if incident.get('latitude') is not None and incident.get('longitude') is not None else {'incident_id': incident.get('id'), 'routes': [], 'simulation': True}
            plans.append({
                'incident_id': incident['id'],
                'incident_title': incident['title'],
                'severity': incident['severity'],
                'priority_score': incident['priority_score'],
                'feasible': feasible,
                'shortages': shortages,
                'selected_resources': [
                    {'id': r['id'], 'label': r.get('label', f"{r['resource_type']} #{r['id']}"), 'resource_type': r['resource_type'], 'hospital_id': r.get('hospital_id'), 'hospital_name': r.get('hospital_name'), 'state_before': r.get('state')}
                    for r in selected
                ],
                'route_logistics': routes,
                'previous_resource_ids': sorted(previous_ids),
                'added_resource_ids': added,
                'removed_resource_ids': removed,
                'reallocation_candidates': reallocation_candidates,
                'human_approval': incident['severity'] == 'CRITICAL' or bool(shortages) or bool(added or removed or reallocation_candidates),
                'explanation': ' '.join(reason_parts)
            })

            if feasible:
                # Keep non-selected resources for lower-priority incidents. A reserved
                # resource selected for reallocation is removed from the next pool pass.
                selected_ids = {r['id'] for r in selected}
                pool = [r for r in pool if r['id'] not in selected_ids]

        return {
            'available_pool_after_plan': self.aggregation.aggregate([r for r in pool if r.get('state') == 'AVAILABLE']),
            'plans': plans,
            'changed_incidents': [p['incident_id'] for p in plans if p['added_resource_ids'] or p['removed_resource_ids'] or p['shortages'] or p['reallocation_candidates']],
            'ai_method': 'Explainable multi-factor resource scoring with priority-aware reallocation proposals',
            'reallocation_candidates': [c for p in plans for c in p['reallocation_candidates']]
        }
