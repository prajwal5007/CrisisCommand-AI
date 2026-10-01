class ConflictDetectionAgent:
    name = 'conflict_detection'
    def check(self, requirements, available):
        shortages = {}
        for resource_type, required in requirements.items():
            have = int(available.get(resource_type, 0))
            if have < int(required):
                shortages[resource_type] = {'required': int(required), 'available': have, 'shortage': int(required) - have}
        return shortages
