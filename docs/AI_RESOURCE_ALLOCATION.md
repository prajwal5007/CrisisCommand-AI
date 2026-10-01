# AI Resource Allocation

CrisisCommand AI uses an explainable resource-scoring engine rather than pretending that a hidden model made an untraceable decision.

## Scoring factors

- Incident priority: severity, urgency and affected people
- Response proximity: incident-to-provider distance
- Resource scarcity: fewer available resources increase scarcity value
- Provider load: avoids concentrating too many selected resources in one provider

Every candidate receives an `ai_score` and a list of factors. Critical or conflicting decisions remain subject to human approval.

## Exhausted resources

When no free resource can satisfy a higher-priority incident, the Dynamic Replanning Agent can propose a **reallocation candidate** from a lower-priority `RESERVED` resource. It does not silently change the existing dispatch. The proposal is marked for human review, and the dashboard explains the source incident, destination incident and reason. `DISPATCHED`, `UNAVAILABLE` and `MAINTENANCE` resources are not reallocated.
