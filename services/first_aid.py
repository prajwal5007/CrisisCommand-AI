"""Conservative, source-based first-aid guidance for the public UI.
This is educational guidance, not diagnosis or a substitute for emergency services.
"""

FIRST_AID = {
    'Road / Car Accident': {
        'title': 'Road accident — while help is coming',
        'steps': [
            'Check that the scene is safe before approaching.',
            'Call 112 for urgent emergency assistance in India; give the exact location.',
            'For life-threatening bleeding, apply firm direct pressure with a clean dressing or cloth. If a limb bleed is life-threatening and you are trained, use a tourniquet according to your training.',
            'Do not move a person with suspected head, neck or spinal injury unless there is immediate danger, CPR is required, or bleeding must be controlled.',
            'Keep the person warm and monitor breathing and responsiveness until responders arrive.'
        ],
        'warning': 'Do not attempt to straighten suspected fractures or move trapped people without appropriate training.'
    },
    'Building Collapse': {
        'title': 'Building collapse — while rescue is coming',
        'steps': [
            'Stay out of unstable structures and watch for secondary collapse, fire, gas or electrical hazards.',
            'Call 112 and report the exact location and number of people believed trapped.',
            'If safe, help people in an open safe area; do not enter rubble to rescue someone unless trained and the area is confirmed safe.',
            'For visible life-threatening bleeding in a safe-to-reach person, apply firm direct pressure.',
            'Do not move someone when a head, neck or spinal injury is suspected unless movement is required for immediate safety or lifesaving care.'
        ],
        'warning': 'Rescuer safety comes first. Follow fire/rescue dispatcher instructions.'
    },
    'Fire / Explosion': {
        'title': 'Fire or explosion — while help is coming',
        'steps': [
            'Move away from fire, smoke, explosions and unstable structures; do not re-enter.',
            'Call 112 and report fire/explosion, hazards and location.',
            'For a burn, cool the affected area with cool running water for 20 minutes when safe to do so; remove nearby jewellery or clothing unless stuck to the skin.',
            'Do not put creams, oils or butter on a burn and do not burst blisters.',
            'If someone is unresponsive, not breathing normally or has another life-threatening condition, call 112 and follow dispatcher instructions; provide care only within your training.'
        ],
        'warning': 'Smoke or chemical exposure can be dangerous even when skin injuries look small.'
    },
    'Flood / Waterlogging': {
        'title': 'Flood or water emergency — while help is coming',
        'steps': [
            'Move to higher, safer ground if you can do so without entering moving water.',
            'Never walk or drive through fast-moving or unknown-depth floodwater.',
            'Call 112 if urgent rescue is required and share your GPS location.',
            'Keep away from fallen electrical wires and suspected electrical hazards.',
            'If someone has been rescued from water and is not breathing normally, call 112 and follow emergency dispatcher instructions.'
        ],
        'warning': 'Floodwater may hide holes, debris, currents and electrical hazards.'
    },
    'Medical Emergency': {
        'title': 'Medical emergency — while help is coming',
        'steps': [
            'Check that the scene is safe and check whether the person responds and is breathing normally.',
            'Call 112 for a life-threatening emergency and put the phone on speaker so the dispatcher can guide you.',
            'If the person is unresponsive and not breathing normally, begin CPR/AED actions only according to dispatcher guidance and your training.',
            'If the person is conscious, keep them in a safe, comfortable position and monitor for changes.',
            'Do not give food, drink or medicines unless a qualified professional or dispatcher specifically advises it.'
        ],
        'warning': 'This guidance does not diagnose the condition. Emergency dispatchers and medical professionals should guide urgent care.'
    },
    'Industrial Accident': {
        'title': 'Industrial accident — while help is coming',
        'steps': [
            'Move to a safe area if there is fire, gas, chemical release, electricity or machinery danger.',
            'Call 112 and identify the industrial hazard if known.',
            'Avoid touching spilled chemicals or contaminated clothing without appropriate protection.',
            'For external life-threatening bleeding, use firm direct pressure if it is safe to approach.',
            'Follow site emergency procedures and responder instructions.'
        ],
        'warning': 'Chemical, electrical and machinery incidents may require specialized rescue teams.'
    },
    'Missing / Trapped People': {
        'title': 'Trapped or missing people — while rescue is coming',
        'steps': [
            'Call 112 and provide the last known location and hazards.',
            'Do not enter a collapsed, flooded, burning or otherwise unsafe area to search unless trained and authorized.',
            'If you can communicate with a trapped person safely, reassure them and ask them to conserve energy.',
            'Report hazards, sounds, signals or changes to the emergency operator.',
            'Keep access routes clear for responding rescue teams.'
        ],
        'warning': 'Do not create a second casualty by entering an unsafe rescue scene.'
    },
}

DEFAULT = {
    'title': 'Emergency — while help is coming',
    'steps': [
        'Check that the scene is safe before approaching.',
        'Call 112 for urgent emergency assistance in India and provide the exact location.',
        'Check responsiveness, breathing and obvious life-threatening bleeding.',
        'Give only first aid that matches your training and follow emergency dispatcher instructions.',
        'Stay with the person when safe and monitor for changes until trained responders arrive.'
    ],
    'warning': 'This is general first-aid information, not a diagnosis or a substitute for professional medical care.'
}


def get_first_aid(title='', description=''):
    title = (title or '').strip()
    if title in FIRST_AID:
        return FIRST_AID[title]
    text = f'{title} {description}'.lower()
    for key, value in FIRST_AID.items():
        if any(token in text for token in key.lower().replace('/', ' ').split()):
            return value
    return DEFAULT
