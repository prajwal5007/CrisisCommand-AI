from collections import Counter

class ResourceAggregationAgent:
    name = 'resource_aggregation'
    def aggregate(self, resources):
        counter = Counter(r['resource_type'] for r in resources if r['state'] == 'AVAILABLE')
        return dict(counter)
