import json
from agents.incident_agent import IncidentAssessmentAgent
from agents.conflict_agent import ConflictDetectionAgent
from agents.replanning_agent import DynamicReplanningAgent
from agents.route_logistics_agent import RouteLogisticsAgent
from agents.ai_allocation_agent import AIResourceAllocationAgent
from services.first_aid import get_first_aid
from services.geo_service import haversine_km


def test_haversine_zero():
    assert haversine_km(12.9,77.6,12.9,77.6) == 0


def test_haversine_reasonable():
    assert 0 < haversine_km(12.9716,77.5946,12.975,77.6) < 1


def test_assessment_critical():
    result=IncidentAssessmentAgent().assess({'severity':'CRITICAL','urgency':10,'affected_people':20,'source_confidence':'VERIFIED','resource_needs':{'AMBULANCE':2}})
    assert result['priority_score'] == 21
    assert result['human_review'] is True



def test_ambulance_capacity_rule_two_people_per_ambulance():
    agent = IncidentAssessmentAgent()
    cases = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 20: 10}
    for affected, expected in cases.items():
        needs = agent.infer_resource_needs({
            'title': 'Road / Car Accident',
            'description': 'vehicle collision with injured people',
            'affected_people': affected,
            'severity': 'HIGH'
        })
        assert needs['AMBULANCE'] == expected


def test_conflict():
    result=ConflictDetectionAgent().check({'AMBULANCE':2,'MEDICAL_TEAM':1},{'AMBULANCE':1,'MEDICAL_TEAM':1})
    assert result['AMBULANCE']['shortage'] == 1


def test_replan_priority_and_shortage():
    incidents=[
      {'id':1,'title':'Critical','severity':'CRITICAL','priority_score':20,'resource_needs':{'AMBULANCE':2}},
      {'id':2,'title':'High','severity':'HIGH','priority_score':10,'resource_needs':{'AMBULANCE':1}},
    ]
    resources=[{'resource_type':'AMBULANCE','state':'AVAILABLE'},{'resource_type':'AMBULANCE','state':'AVAILABLE'}]
    result=DynamicReplanningAgent().replan(incidents,resources)
    assert result['plans'][0]['feasible'] is True
    assert result['plans'][1]['shortages']['AMBULANCE']['shortage'] == 1


def test_route_logistics_simulation():
    result = RouteLogisticsAgent().plan(
        {'id': 1, 'latitude': 12.9716, 'longitude': 77.5946},
        [{'id': 7, 'label': 'A-Amb-1', 'resource_type': 'AMBULANCE',
          'hospital_id': 2, 'hospital_name': 'Hospital A',
          'latitude': 12.9750, 'longitude': 77.6000}]
    )
    assert result['simulation'] is True
    assert result['routes'][0]['estimated_eta_minutes'] >= 2


def test_replan_selects_actual_resources_and_explains_change():
    incidents = [{
        'id': 1, 'title': 'Critical Accident', 'severity': 'CRITICAL',
        'priority_score': 20, 'resource_needs': {'AMBULANCE': 1},
        'latitude': 12.9716, 'longitude': 77.5946
    }]
    resources = [{
        'id': 11, 'label': 'A-Amb-1', 'resource_type': 'AMBULANCE',
        'state': 'AVAILABLE', 'hospital_id': 2, 'hospital_name': 'Hospital A',
        'latitude': 12.9750, 'longitude': 77.6000
    }]
    result = DynamicReplanningAgent().replan(
        incidents, resources,
        previous_allocations=[{'incident_id': 1, 'resource_id': 99, 'status': 'RESERVED'}]
    )
    item = result['plans'][0]
    assert item['feasible'] is True
    assert item['added_resource_ids'] == [11]
    assert item['removed_resource_ids'] == [99]
    assert item['human_approval'] is True
    assert item['route_logistics']['routes']


def test_ai_allocation_scores_available_resource():
    agent = AIResourceAllocationAgent()
    ranked = agent.rank(
        {'severity':'CRITICAL','urgency':10,'affected_people':20,'latitude':12.9716,'longitude':77.5946},
        [{'id':1,'resource_type':'AMBULANCE','state':'AVAILABLE','hospital_id':1,'latitude':12.9720,'longitude':77.5950},
         {'id':2,'resource_type':'AMBULANCE','state':'AVAILABLE','hospital_id':2,'latitude':13.10,'longitude':77.70}]
    )
    assert ranked[0]['id'] == 1
    assert 'ai_score' in ranked[0]


def test_replan_can_propose_reallocation_from_lower_priority():
    incidents=[
      {'id':1,'title':'Critical New Incident','severity':'CRITICAL','priority_score':20,'resource_needs':{'AMBULANCE':1},'latitude':12.9716,'longitude':77.5946},
      {'id':2,'title':'High Existing Incident','severity':'HIGH','priority_score':10,'resource_needs':{'AMBULANCE':1},'latitude':12.98,'longitude':77.60},
    ]
    resources=[{'id':50,'resource_type':'AMBULANCE','state':'RESERVED','hospital_id':2,'hospital_name':'Hospital B','latitude':12.97,'longitude':77.59}]
    result=DynamicReplanningAgent().replan(incidents, resources, previous_allocations=[{'incident_id':2,'resource_id':50,'status':'RESERVED'}])
    item=result['plans'][0]
    assert item['feasible'] is True
    assert item['reallocation_candidates'][0]['from_incident_id'] == 2
    assert item['human_approval'] is True


def test_first_aid_has_emergency_boundary():
    aid=get_first_aid('Road / Car Accident','')
    assert '112' in ' '.join(aid['steps'])
    assert aid['steps']
