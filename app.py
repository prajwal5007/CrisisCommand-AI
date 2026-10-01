import json
import secrets
import uuid
import hashlib
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json as _json
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash

from config import Config
from database.db import query, get_connection, transaction, health_check
from security import verify_password, hash_password
from api.auth import require_role, current_user, ROLE_HOME
from agents.incident_agent import IncidentAssessmentAgent
from agents.hospital_agent import HospitalCoordinationAgent
from agents.aggregation_agent import ResourceAggregationAgent
from agents.conflict_agent import ConflictDetectionAgent
from agents.allocation_agent import ResourceAllocationAgent
from agents.command_planning import CommandPlanningAgent
from agents.replanning_agent import DynamicReplanningAgent
from agents.route_logistics_agent import RouteLogisticsAgent
from services.resource_service import available_resources, set_resource_state
from services.audit_service import audit
from services.first_aid import FIRST_AID, DEFAULT, get_first_aid

app = Flask(__name__)
app.config.from_object(Config)


def send_password_reset_email(recipient, reset_url):
    host = Config.SMTP_HOST
    port = Config.SMTP_PORT
    username = Config.SMTP_USERNAME
    password = Config.SMTP_PASSWORD
    sender = Config.SMTP_FROM or username
    if not host or not sender:
        print(f'PASSWORD RESET DEMO LINK for {recipient}: {reset_url}', flush=True)
        return False
    msg = EmailMessage()
    msg['Subject'] = 'CrisisCommand AI - Password Reset'
    msg['From'] = sender
    msg['To'] = recipient
    msg.set_content(
        'You requested a password reset for CrisisCommand AI.\n\n'
        f'Use this link within 30 minutes:\n{reset_url}\n\n'
        'If you did not request this, you can ignore this email.'
    )
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)
    return True


def csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


@app.context_processor
def inject_globals():
    return {'current_user': current_user(), 'csrf_token': csrf_token()}


def require_csrf(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        expected = session.get('csrf_token')
        provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not expected or not provided or not secrets.compare_digest(expected, provided):
            return jsonify({'error': 'CSRF validation failed'}), 400
        return function(*args, **kwargs)
    return wrapper


def parse_resources(payload):
    allowed = {'AMBULANCE', 'MEDICAL_TEAM', 'RESCUE_TEAM', 'SHELTER'}
    raw = payload.get('required_resources') or payload.get('resource_needs') or {}
    if not isinstance(raw, dict):
        raise ValueError('required_resources must be an object')
    clean = {}
    for key, value in raw.items():
        key = str(key).upper()
        if key not in allowed:
            raise ValueError(f'Unsupported resource type: {key}')
        amount = int(value)
        if amount < 0 or amount > 50:
            raise ValueError(f'Invalid quantity for {key}')
        if amount:
            clean[key] = amount
    return clean



def validate_email(value):
    value = str(value or '').strip().lower()
    if not value or len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", value):
        raise ValueError('Please enter a valid email address.')
    return value


def validate_contact(value, required=True):
    value = str(value or '').strip()
    if not value and not required:
        return ''
    if len(value) < 7 or len(value) > 25 or not re.fullmatch(r"[0-9+()\-\s]{7,25}", value):
        raise ValueError('Please enter a valid contact number (7–25 digits/symbols).')
    digits = re.sub(r'\D', '', value)
    if len(digits) < 7 or len(digits) > 15:
        raise ValueError('Contact number must contain 7–15 digits.')
    return value


def validate_text(value, label, min_len=2, max_len=255, required=True):
    value = str(value or '').strip()
    if not value and not required:
        return ''
    if len(value) < min_len or len(value) > max_len:
        raise ValueError(f'{label} must be between {min_len} and {max_len} characters.')
    return value

def validate_coordinates(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid latitude/longitude')
    return lat, lon


def log_agent(name, input_data, output_data=None, error=None):
    execution_id = str(uuid.uuid4())
    status = 'FAILED' if error else 'SUCCESS'
    query(
        '''INSERT INTO agent_runs (agent_name,execution_id,status,input_json,output_json,error,finished_at)
           VALUES (%s,%s,%s,%s,%s,%s,NOW())''',
        (name, execution_id, status, json.dumps(input_data, default=str),
         json.dumps(output_data, default=str) if output_data is not None else None, error)
    )
    return execution_id


def incident_needs(incident):
    value = incident.get('required_resources')
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def create_hospital_requests(incident_id, lat, lon, needs, actor_id):
    result = HospitalCoordinationAgent().find_nearby(lat, lon, needs)
    log_agent('nearby_hospital_coordination', {'incident_id': incident_id, 'latitude': lat, 'longitude': lon, 'requirements': needs}, result)
    for hospital in result['hospitals']:
        request_id = query(
            '''INSERT INTO hospital_requests (incident_id,hospital_id,distance_km,requested_resources,response_status,response_data)
               VALUES (%s,%s,%s,%s,'CONTACTED',%s)
               ON DUPLICATE KEY UPDATE distance_km=VALUES(distance_km), requested_resources=VALUES(requested_resources), response_status='CONTACTED', response_data=VALUES(response_data), contacted_at=NOW()''',
            (incident_id, hospital['id'], hospital['distance_km'], json.dumps(needs), json.dumps({'available': hospital['available']}))
        )
        priority = 'CRITICAL' if incident.get('severity') == 'CRITICAL' else 'HIGH' if incident.get('severity') == 'HIGH' else 'NORMAL'
        incident_rows = query('SELECT title,severity,location_text FROM incidents WHERE id=%s', (incident_id,), True)
        incident = incident_rows[0] if incident_rows else {}
        req_lines = ', '.join(f"{k.replace('_',' ').title()}: {v}" for k,v in needs.items())
        query('''INSERT INTO notifications (recipient_type,recipient_id,incident_id,priority,title,message)
                 VALUES ('HOSPITAL',%s,%s,%s,%s,%s)''',
              (hospital['id'], incident_id, priority,
               f"{priority} emergency resource request",
               f"{incident.get('title','Emergency')} at {incident.get('location_text','incident location')}. Command Center requests: {req_lines}. Review your current availability and accept, partially accept, or reject this request."))
    query("UPDATE incidents SET status='COORDINATING' WHERE id=%s", (incident_id,))
    audit(actor_id, 'HOSPITALS_CONTACTED', 'INCIDENT', incident_id, json.dumps({'radius_km': result['radius_km'], 'hospital_ids': [h['id'] for h in result['hospitals']], 'expanded': result['expanded']}))
    return result


def run_replan(actor_id, trigger='MANUAL'):
    incidents = query(
        "SELECT * FROM incidents WHERE status NOT IN ('RESOLVED') AND human_decision='APPROVED' ORDER BY FIELD(severity,'CRITICAL','HIGH','MEDIUM','LOW'), id",
        fetch=True
    )
    resources = query(
        "SELECT r.*, o.name AS hospital_name "
        "FROM resources r JOIN organizations o ON o.id=r.hospital_id "
        "WHERE r.state IN ('AVAILABLE','RESERVED') AND o.verified=TRUE",
        fetch=True
    )
    prepared = []
    for incident in incidents:
        needs = incident_needs(incident)
        assessment = IncidentAssessmentAgent().assess(
            {**incident, 'resource_needs': needs}
        )
        prepared.append({
            **incident,
            'resource_needs': needs,
            'priority_score': assessment['priority_score']
        })

    previous_allocations = query(
        "SELECT a.incident_id,a.resource_id,a.status FROM allocations a "
        "WHERE a.status IN ('PROPOSED','RESERVED','DISPATCHED')",
        fetch=True
    )
    request_rows = query("SELECT incident_id,response_data,response_status FROM hospital_requests WHERE response_status IN ('APPROVED','PARTIAL')", fetch=True)
    eligible_by_incident = {}
    for row in request_rows:
        data=row.get('response_data') or {}
        if isinstance(data,str):
            try:data=json.loads(data)
            except Exception:data={}
        eligible_by_incident.setdefault(row['incident_id'],set()).update(data.get('resource_ids') or [])
    eligible_by_incident = {k:set(int(x) for x in v if str(x).isdigit()) for k,v in eligible_by_incident.items()}
    result = DynamicReplanningAgent().replan(
        prepared, resources, previous_allocations=previous_allocations, eligible_by_incident=eligible_by_incident
    )

    previous = query(
        'SELECT id,version,status FROM plans ORDER BY version DESC LIMIT 1',
        fetch=True
    )
    version = (previous[0]['version'] + 1) if previous else 1
    if previous:
        query("UPDATE plans SET status='SUPERSEDED' WHERE id=%s AND status<>'SUPERSEDED'", (previous[0]['id'],))

    explanation = {
        'trigger': trigger,
        'changed_incidents': result.get('changed_incidents', []),
        'summary': (
            'AI reassessment completed. The revised plan uses the latest verified resource state, '
            'scores feasible resources by priority/proximity/scarcity/provider load, and proposes '
            'human-reviewed reallocation when a higher-priority incident has exhausted free resources.'
        ),
        'ai_method': result.get('ai_method'),
        'reallocation_candidates': result.get('reallocation_candidates', []),
        'human_review': [
            p['incident_id'] for p in result['plans'] if p['human_approval']
        ]
    }
    plan_id = query(
        "INSERT INTO plans (version,status,created_by,explanation) "
        "VALUES (%s,'PROPOSED',%s,%s)",
        (version, actor_id, json.dumps(explanation))
    )

    for item in result['plans']:
        if item['feasible']:
            selected = item.get('selected_resources', [])
            for resource in selected:
                route_text = '; '.join(
                    f"{r.get('resource_label') or r.get('resource_id')}: {r.get('estimated_eta_minutes')} min ETA"
                    for r in item.get('route_logistics', {}).get('routes', [])
                )
                reason = item['explanation'] + (f" Route/ETA: {route_text}." if route_text else '')
                query(
                    "INSERT INTO plan_items "
                    "(plan_id,incident_id,resource_id,action,reason) "
                    "VALUES (%s,%s,%s,'ALLOCATE',%s)",
                    (plan_id, item['incident_id'], resource['id'], reason)
                )
        else:
            query(
                "INSERT INTO plan_items "
                "(plan_id,incident_id,resource_id,action,reason) "
                "VALUES (%s,%s,NULL,'SHORTAGE',%s)",
                (plan_id, item['incident_id'], item['explanation'])
            )

    log_agent(
        'dynamic_replanning',
        {'trigger': trigger, 'incident_count': len(prepared)},
        result
    )
    audit(
        actor_id, 'PLAN_REPLANNED', 'PLAN', plan_id,
        json.dumps({'version': version, 'trigger': trigger, 'result': result})
    )
    return {
        'plan_id': plan_id,
        'version': version,
        'trigger': trigger,
        **result,
        'explanation': explanation
    }

@app.get('/first-aid')
def first_aid_page():
    data = dict(FIRST_AID)
    data['__DEFAULT__'] = DEFAULT
    return render_template('first_aid.html', first_aid=data, first_aid_keys=list(FIRST_AID.keys()))


@app.get('/api/first-aid')
def first_aid_api():
    title = request.args.get('title', '')
    description = request.args.get('description', '')
    return jsonify(get_first_aid(title, description))


@app.get('/health')
def health():
    ok, error = health_check()
    return {'status': 'ok' if ok else 'degraded', 'service': 'crisiscommand', 'database': ok, 'error': error}, (200 if ok else 503)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", email):
            flash('Please enter a valid email address.', 'error')
            return render_template('login.html', demo_admin_email=Config.DEMO_ADMIN_EMAIL, demo_admin_password=Config.DEMO_ADMIN_PASSWORD), 400
        if not password or len(password) > 128:
            flash('Please enter a valid password.', 'error')
            return render_template('login.html', demo_admin_email=Config.DEMO_ADMIN_EMAIL, demo_admin_password=Config.DEMO_ADMIN_PASSWORD), 400
        print(f"LOGIN POST received for {email}", flush=True)
        try:
            print("LOGIN: querying database...", flush=True)
            rows = query('''SELECT u.*, o.verified AS hospital_verified
                             FROM users u LEFT JOIN organizations o ON o.id=u.organization_id
                             WHERE u.email=%s AND u.active=TRUE
                             AND (u.role <> 'HOSPITAL_OPERATOR' OR o.verified=TRUE)''', (email,), True)
            print(f"LOGIN: database returned {len(rows)} matching row(s)", flush=True)
            if not rows or not verify_password(password, rows[0]['password_hash']):
                flash('Invalid email or password.', 'error')
                return render_template('login.html'), 401
            user = rows[0]
            role = user.get('role')
            if role not in ROLE_HOME:
                raise ValueError(f'Unsupported user role: {role}')
            session.clear()
            session['user_id'] = user['id']
            session['csrf_token'] = secrets.token_urlsafe(32)
            print(f"LOGIN SUCCESS: user_id={user['id']} role={role}", flush=True)
            return redirect(url_for(ROLE_HOME[role]))
        except Exception as exc:
            print(f"LOGIN ERROR: {type(exc).__name__}: {exc}", flush=True)
            app.logger.exception("Login failed")
            flash(f'Login error: {exc}', 'error')
            return render_template('login.html'), 500
    return render_template('login.html', demo_admin_email=Config.DEMO_ADMIN_EMAIL, demo_admin_password=Config.DEMO_ADMIN_PASSWORD)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", email):
            flash('Please enter a valid registered email address.', 'error')
            return render_template('forgot_password.html'), 400
        message = 'If that email is registered, a password reset link has been sent.'
        try:
            rows = query('SELECT id,email,active FROM users WHERE email=%s AND active=TRUE', (email,), True) if email else []
            if rows:
                raw_token = secrets.token_urlsafe(48)
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                query('DELETE FROM password_reset_tokens WHERE user_id=%s', (rows[0]['id'],))
                query('INSERT INTO password_reset_tokens (user_id,token_hash,expires_at) VALUES (%s,%s,DATE_ADD(NOW(), INTERVAL 30 MINUTE))', (rows[0]['id'], token_hash))
                reset_url = url_for('reset_password', token=raw_token, _external=True)
                sent = send_password_reset_email(rows[0]['email'], reset_url)
                if not sent:
                    flash('Demo mode: SMTP is not configured. The reset link was printed in the Flask terminal.', 'success')
                else:
                    flash('Password reset link sent to the registered email address.', 'success')
            else:
                flash(message, 'success')
        except Exception as exc:
            app.logger.exception('Password reset request failed')
            flash('Unable to process the reset request right now. Please try again.', 'error')
        return render_template('forgot_password.html')
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = query('SELECT pr.id,pr.user_id,u.email FROM password_reset_tokens pr JOIN users u ON u.id=pr.user_id WHERE pr.token_hash=%s AND pr.expires_at>NOW() AND u.active=TRUE', (token_hash,), True)
    if not rows:
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8 or len(password) > 12:
            flash('Password must be between 8 and 12 characters.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            query('UPDATE users SET password_hash=%s WHERE id=%s', (hash_password(password), rows[0]['user_id']))
            query('DELETE FROM password_reset_tokens WHERE id=%s', (rows[0]['id'],))
            flash('Password changed successfully. You can now sign in.', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html', email=rows[0]['email'])


@app.post('/logout')
@require_role('ADMIN', 'HOSPITAL_OPERATOR')
@require_csrf
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.get('/')
def home():
    user = current_user()
    return redirect(url_for(ROLE_HOME[user['role']])) if user else redirect(url_for('user_module'))


@app.get('/user')
def user_module():
    return render_template('user.html')


@app.post('/user/incidents')
@require_csrf
def user_create_incident():
    payload = request.get_json(silent=True) or request.form
    try:
        title = str(payload.get('title', '')).strip()
        description = str(payload.get('description', '')).strip()
        location = str(payload.get('location_text', '')).strip()
        contact_info = str(payload.get('contact_info', '')).strip()
        additional_notes = str(payload.get('additional_notes', '')).strip()
        affected = int(payload.get('affected_people', 0))
        if not title or len(title) < 3 or len(title) > 120:
            raise ValueError('Emergency type/title must be between 3 and 120 characters.')
        if not description or len(description) < 5 or len(description) > 2000:
            raise ValueError('Please describe what happened (5–2000 characters).')
        if affected < 1 or affected > 100000:
            raise ValueError('Affected people must be between 1 and 100000.')
        contact_info = validate_contact(contact_info, True)
        if additional_notes and len(additional_notes) > 2000:
            raise ValueError('Additional information is too long (maximum 2000 characters).')
        raw_lat = payload.get('latitude')
        raw_lon = payload.get('longitude')
        if raw_lat in (None, '', 'null') or raw_lon in (None, '', 'null'):
            raise ValueError('Please select the emergency location on the map or enable Live GPS.')
        lat, lon = validate_coordinates(raw_lat, raw_lon)
        if not location or location == 'Map location selected':
            raise ValueError('Please select a location on the map and wait for the locality to be identified.')
        draft = {
            'title': title, 'description': description, 'additional_notes': additional_notes,
            'affected_people': affected, 'source': 'PUBLIC', 'latitude': lat, 'longitude': lon,
            'severity': '', 'urgency': 0, 'source_confidence': 'UNVERIFIED', 'auto_assess': True
        }
        preliminary = IncidentAssessmentAgent().assess(draft)
        needs = preliminary['resource_needs']
        severity = preliminary['severity']
        urgency = preliminary['urgency']
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    incident_id = query(
        'INSERT INTO incidents (title,description,source,location_text,contact_info,additional_notes,latitude,longitude,severity,urgency,affected_people,required_resources,confidence,source_confidence,status,created_by,original_input) '
        "VALUES (%s,%s,'PUBLIC',%s,%s,%s,%s,%s,%s,%s,%s,%s,'MEDIUM','UNVERIFIED','SUBMITTED',NULL,%s)",
        (title, description, location, contact_info, additional_notes, lat, lon, severity, urgency, affected, json.dumps(needs), json.dumps(dict(payload), default=str))
    )
    audit(None, 'CRISIS_SUBMITTED', 'INCIDENT', incident_id, json.dumps({'title': title, 'source': 'PUBLIC', 'auto_assessment': {'severity': severity, 'urgency': urgency, 'resource_needs': needs}}))
    return jsonify({'status': 'submitted', 'incident_id': incident_id, 'severity': severity, 'urgency': urgency, 'resource_needs': needs,
                    'first_aid': get_first_aid(title, description),
                    'hospital_requests_sent': 0,
                    'replan_triggered': False,
                    'replan_version': None,
                    'human_decision': 'PENDING',
                    'message': 'Emergency submitted. An authorized human operator must approve the incident before nearby hospitals are contacted and resources are requested.'}), 201


@app.get('/api/geocode/search')
def search_geocode():
    try:
        q = str(request.args.get('q', '')).strip()
        if len(q) < 2:
            return jsonify({'results': []})
        params = urlencode({'format': 'jsonv2', 'q': q, 'limit': 5, 'addressdetails': 1, 'countrycodes': 'in'})
        req = Request(
            'https://nominatim.openstreetmap.org/search?' + params,
            headers={'User-Agent': 'CrisisCommandAI/1.0 emergency-coordination-demo'}
        )
        with urlopen(req, timeout=7) as response:
            data = _json.loads(response.read().decode('utf-8'))
        results = []
        for item in data:
            address = item.get('address') or {}
            locality = (address.get('suburb') or address.get('neighbourhood') or address.get('village') or
                        address.get('town') or address.get('city_district') or address.get('city') or
                        address.get('municipality') or address.get('county') or '')
            results.append({
                'lat': float(item['lat']),
                'lon': float(item['lon']),
                'display_name': item.get('display_name') or '',
                'locality': locality
            })
        return jsonify({'results': results})
    except Exception as exc:
        return jsonify({'error': 'Unable to search the map location right now', 'detail': str(exc)}), 502


@app.get('/api/geocode/reverse')
def reverse_geocode():
    try:
        lat, lon = validate_coordinates(request.args.get('lat'), request.args.get('lon'))
        params = urlencode({'format': 'jsonv2', 'lat': lat, 'lon': lon, 'zoom': 18, 'addressdetails': 1})
        req = Request(
            'https://nominatim.openstreetmap.org/reverse?' + params,
            headers={'User-Agent': 'CrisisCommandAI/1.0 emergency-coordination-demo'}
        )
        with urlopen(req, timeout=5) as response:
            data = _json.loads(response.read().decode('utf-8'))
        address = data.get('address') or {}
        locality = (address.get('suburb') or address.get('neighbourhood') or address.get('village') or
                    address.get('town') or address.get('city_district') or address.get('city') or
                    address.get('municipality') or address.get('county') or '')
        display = data.get('display_name') or locality
        return jsonify({'locality': locality, 'display_name': display, 'address': address})
    except Exception as exc:
        return jsonify({'error': 'Unable to determine locality from GPS right now', 'detail': str(exc)}), 502


@app.route('/hospital/register', methods=['GET', 'POST'])
def hospital_register():
    if request.method == 'GET':
        return render_template('hospital_register.html')
    payload = request.get_json(silent=True) or request.form
    try:
        name = validate_text(payload.get('hospital_name'), 'Hospital name', 2, 120)
        contact = validate_contact(payload.get('contact'), True)
        email = validate_email(payload.get('email'))
        password = str(payload.get('password', ''))
        if len(password) < 8 or len(password) > 12:
            raise ValueError('Password must be 8–12 characters.')
        latitude, longitude = validate_coordinates(payload.get('latitude'), payload.get('longitude'))
        location = validate_text(payload.get('location'), 'Hospital locality/address', 3, 255)


        # Resource inventory declared during registration. These values are used to
        # create the hospital's initial allocatable resource pool.
        resource_counts = {}
        for key in ('AMBULANCE', 'MEDICAL_TEAM', 'RESCUE_TEAM', 'SHELTER'):
            raw = payload.get(key.lower())
            amount = int(raw or 0)
            if amount < 0 or amount > 100:
                raise ValueError(f'Invalid quantity for {key}')
            resource_counts[key] = amount

        capacity_counts = {}
        for key in ('general_beds', 'icu_beds', 'oxygen_beds', 'trauma_beds', 'doctors', 'nurses', 'blood_units'):
            amount = int(payload.get(key) or 0)
            if amount < 0 or amount > 10000:
                raise ValueError(f'Invalid quantity for {key}')
            capacity_counts[key] = amount
    except (TypeError, ValueError) as exc:
        if request.is_json:
            return jsonify({'error': str(exc)}), 400
        flash(str(exc), 'error')
        return render_template('hospital_register.html'), 400
    existing = query('SELECT id FROM users WHERE email=%s', (email,), True)
    if existing:
        msg = 'This email is already registered.'
        if request.is_json: return jsonify({'error': msg}), 409
        flash(msg, 'error'); return render_template('hospital_register.html'), 409
    org_id = query('''INSERT INTO organizations (name,organization_type,verified,contact,latitude,longitude,location)
                      VALUES (%s,'HOSPITAL',FALSE,%s,%s,%s,%s)''',
                   (name, contact, latitude, longitude, location))
    user_id = query('''INSERT INTO users (organization_id,name,email,password_hash,role,active)
                       VALUES (%s,%s,%s,%s,'HOSPITAL_OPERATOR',FALSE)''',
                    (org_id, name + ' Operator', email, hash_password(password)))

    query('''INSERT INTO hospital_capacities
             (hospital_id,general_beds,icu_beds,oxygen_beds,trauma_beds,doctors,nurses,blood_units)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
          (org_id, capacity_counts['general_beds'], capacity_counts['icu_beds'],
           capacity_counts['oxygen_beds'], capacity_counts['trauma_beds'],
           capacity_counts['doctors'], capacity_counts['nurses'], capacity_counts['blood_units']))

    # Create one resource record per registered emergency unit so the AI allocator
    # can immediately score real, individually trackable resources.
    labels = {
        'AMBULANCE': 'Ambulance',
        'MEDICAL_TEAM': 'Medical Team',
        'RESCUE_TEAM': 'Rescue Team',
        'SHELTER': 'Shelter'
    }
    for rtype, amount in resource_counts.items():
        for idx in range(1, amount + 1):
            query('''INSERT INTO resources (hospital_id,resource_type,label,state,capabilities,latitude,longitude)
                     VALUES (%s,%s,%s,'AVAILABLE',%s,%s,%s)''',
                  (org_id, rtype, f"{labels[rtype]}-{idx}",
                   json.dumps([]), latitude, longitude))

    audit(None, 'HOSPITAL_REGISTRATION_SUBMITTED', 'ORGANIZATION', org_id,
          json.dumps({'hospital_user_id': user_id, 'name': name, 'email': email,
                      'resource_counts': resource_counts, 'capacity_counts': capacity_counts}))
    result = {'status': 'pending_verification', 'hospital_id': org_id, 'message': 'Registration submitted with the declared resource inventory. An admin must verify the hospital before login is enabled.'}
    if request.is_json: return jsonify(result), 201
    return render_template('hospital_register_success.html', hospital_name=name)


@app.get('/api/hospitals/pending')
@require_role('ADMIN')
def pending_hospitals():
    return jsonify(query('''SELECT o.id,o.name,o.contact,o.latitude,o.longitude,o.location,o.created_at,u.email,u.active
                            FROM organizations o JOIN users u ON u.organization_id=o.id
                            WHERE o.organization_type='HOSPITAL' AND o.verified=FALSE
                            ORDER BY o.created_at DESC''', fetch=True))


@app.post('/api/hospitals/<int:hospital_id>/verify')
@require_role('ADMIN')
@require_csrf
def verify_hospital(hospital_id):
    rows = query("SELECT * FROM organizations WHERE id=%s AND organization_type='HOSPITAL'", (hospital_id,), True)
    if not rows: return jsonify({'error': 'Hospital not found'}), 404
    query('UPDATE organizations SET verified=TRUE WHERE id=%s', (hospital_id,))
    query("UPDATE users SET active=TRUE WHERE organization_id=%s AND role='HOSPITAL_OPERATOR'", (hospital_id,))
    audit(request.operator['id'], 'HOSPITAL_VERIFIED', 'ORGANIZATION', hospital_id, '{}')
    return jsonify({'status': 'verified', 'hospital_id': hospital_id})


@app.post('/api/hospitals/<int:hospital_id>/reject')
@require_role('ADMIN')
@require_csrf
def reject_hospital(hospital_id):
    rows = query("SELECT * FROM organizations WHERE id=%s AND organization_type='HOSPITAL'", (hospital_id,), True)
    if not rows: return jsonify({'error': 'Hospital not found'}), 404
    query("UPDATE users SET active=FALSE WHERE organization_id=%s AND role='HOSPITAL_OPERATOR'", (hospital_id,))
    audit(request.operator['id'], 'HOSPITAL_REGISTRATION_REJECTED', 'ORGANIZATION', hospital_id, '{}')
    return jsonify({'status': 'rejected', 'hospital_id': hospital_id})


@app.get('/api/public/incidents/<int:incident_id>')
def public_incident_status(incident_id):
    rows = query('''SELECT id,title,location_text,latitude,longitude,severity,urgency,status,affected_people,required_resources,created_at,updated_at
                    FROM incidents WHERE id=%s''', (incident_id,), True)
    if not rows: return jsonify({'error': 'Incident not found'}), 404
    incident = rows[0]
    requests = query('''SELECT hr.id,hr.hospital_id,o.name AS hospital_name,hr.distance_km,hr.requested_resources,hr.response_status,hr.response_data,hr.responded_at
                        FROM hospital_requests hr JOIN organizations o ON o.id=hr.hospital_id
                        WHERE hr.incident_id=%s ORDER BY hr.distance_km''', (incident_id,), True)
    allocations = query('''SELECT a.id,a.status,a.created_at,a.approved_at,r.id AS resource_id,r.label,r.resource_type,r.state,r.latitude,r.longitude,o.name AS hospital_name
                          FROM allocations a JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id
                          WHERE a.incident_id=%s ORDER BY a.id''', (incident_id,), True)
    notifications = query('''SELECT id,priority,title,message,created_at FROM notifications WHERE incident_id=%s AND recipient_type='USER' ORDER BY created_at DESC''', (incident_id,), True)
    for r in requests:
        for key in ('requested_resources','response_data'):
            if isinstance(r.get(key), str):
                try: r[key] = json.loads(r[key])
                except Exception: pass
    return jsonify({**incident, 'hospital_requests': requests, 'allocations': allocations, 'notifications': notifications})


@app.get('/api/admin/live-summary')
@require_role('ADMIN')
def admin_live_summary():
    incidents = query("SELECT id,severity,status,human_decision FROM incidents", fetch=True)
    resources = query("SELECT state FROM resources", fetch=True)
    approvals = query("SELECT COUNT(*) AS n FROM allocations WHERE status='PROPOSED'", fetch=True)
    incident_approvals = query("SELECT COUNT(*) AS n FROM incidents WHERE human_decision='PENDING'", fetch=True)
    critical = sum(1 for i in incidents if i['severity']=='CRITICAL' and i['status'] not in ('RESOLVED','REJECTED'))
    high = sum(1 for i in incidents if i['severity']=='HIGH' and i['status'] not in ('RESOLVED','REJECTED'))
    available = sum(1 for r in resources if r['state']=='AVAILABLE')
    resolved_ids = [i['id'] for i in incidents if i['status'] in ('RESOLVED','REJECTED')]
    active_statuses = [{'id':i['id'],'status':i['status']} for i in incidents if i['status'] not in ('RESOLVED','REJECTED')]
    return jsonify({'critical':critical,'high':high,'available':available,'pending':int(approvals[0]['n'])+int(incident_approvals[0]['n']),'resolved_ids':resolved_ids,'active_statuses':active_statuses})

@app.get('/api/admin/notifications')
@require_role('ADMIN')
def admin_notifications_api():
    rows = query("""SELECT id,action,entity_type,entity_id,details_json,created_at
                    FROM audit_logs
                    WHERE action IN (
                      'CRISIS_SUBMITTED','INCIDENT_APPROVED','INCIDENT_REJECTED',
                      'HOSPITAL_REQUEST_ACCEPTED','HOSPITAL_REQUEST_PARTIAL','HOSPITAL_REQUEST_DECLINED',
                      'ALLOCATION_PROPOSED','ALLOCATION_APPROVED','ALLOCATION_REJECTED',
                      'RESOURCE_OPERATION_COMPLETED','RESOURCE_LOCATION_UPDATED',
                      'ALLOCATION_SHORTAGE','DYNAMIC_REPLANNING'
                    ) ORDER BY created_at DESC LIMIT 40""", fetch=True)
    for r in rows:
        raw=r.get('details_json')
        if isinstance(raw,str):
            try:r['details']=json.loads(raw)
            except Exception:r['details']={}
        else:r['details']=raw or {}
        r['label']=r.get('action','').replace('_',' ').title()
    return jsonify({'notifications':rows})

@app.get('/api/admin/live-map')
@require_role('ADMIN')
def admin_live_map():
    incidents=query("""SELECT id,title,severity,status,latitude,longitude,location_text FROM incidents
                       WHERE status NOT IN ('RESOLVED','REJECTED') AND latitude IS NOT NULL AND longitude IS NOT NULL
                       ORDER BY created_at DESC""",fetch=True)
    hospitals=query("""SELECT id,name,latitude,longitude,verified,location FROM organizations
                       WHERE organization_type='HOSPITAL' AND verified=TRUE AND latitude IS NOT NULL AND longitude IS NOT NULL""",fetch=True)
    resources=query("""SELECT r.id,r.label,r.resource_type,r.state,r.latitude,r.longitude,o.name AS hospital_name
                      FROM resources r JOIN organizations o ON o.id=r.hospital_id
                      WHERE r.latitude IS NOT NULL AND r.longitude IS NOT NULL""",fetch=True)
    return jsonify({'incidents':incidents,'hospitals':hospitals,'resources':resources})

@app.get('/admin')
@require_role('ADMIN')
def admin_module():
    incidents = query('SELECT * FROM incidents ORDER BY FIELD(severity,"CRITICAL","HIGH","MEDIUM","LOW"), created_at DESC', fetch=True)
    resources = query('SELECT r.*, o.name AS hospital_name FROM resources r JOIN organizations o ON o.id=r.hospital_id ORDER BY o.name,r.id', fetch=True)
    approvals = query('''SELECT a.*, i.title, i.severity, r.label, r.resource_type, o.name AS hospital_name FROM allocations a JOIN incidents i ON i.id=a.incident_id JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id WHERE a.status='PROPOSED' ORDER BY a.created_at DESC''', fetch=True)
    incident_approvals = query("SELECT * FROM incidents WHERE human_decision='PENDING' ORDER BY FIELD(severity,'CRITICAL','HIGH','MEDIUM','LOW'), created_at DESC", fetch=True)
    hospitals = query("SELECT * FROM organizations WHERE organization_type='HOSPITAL' ORDER BY name", fetch=True)
    agents = query('SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 20', fetch=True)
    logs = query('SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 30', fetch=True)
    plans = query('SELECT * FROM plans ORDER BY version DESC LIMIT 1', fetch=True)
    hospital_requests = query('''SELECT hr.*,i.title,i.severity,i.location_text,o.name AS hospital_name
                                 FROM hospital_requests hr JOIN incidents i ON i.id=hr.incident_id JOIN organizations o ON o.id=hr.hospital_id
                                 ORDER BY hr.contacted_at DESC LIMIT 50''', fetch=True)
    active_allocations = query('''SELECT a.id,a.status,a.proposal_id,a.created_at,a.approved_at,a.reason,a.priority_basis,
                                        i.id AS incident_id,i.title,i.severity,i.location_text,i.latitude AS incident_latitude,i.longitude AS incident_longitude,
                                        r.id AS resource_id,r.label,r.resource_type,r.state,r.latitude,r.longitude,
                                        o.id AS hospital_id,o.name AS hospital_name,o.contact AS hospital_contact
                                 FROM allocations a JOIN incidents i ON i.id=a.incident_id
                                 JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id
                                 WHERE a.status IN ('RESERVED','DISPATCHED')
                                 ORDER BY COALESCE(a.approved_at,a.created_at) DESC LIMIT 100''', fetch=True)
    for hreq in hospital_requests:
        for key in ('requested_resources','response_data'):
            if isinstance(hreq.get(key),str):
                try:hreq[key]=json.loads(hreq[key])
                except Exception:pass
    stats = {
        'critical': sum(1 for i in incidents if i['severity']=='CRITICAL' and i['status']!='RESOLVED'),
        'high': sum(1 for i in incidents if i['severity']=='HIGH' and i['status']!='RESOLVED'),
        'available': sum(1 for r in resources if r['state']=='AVAILABLE'),
        'approvals': len(approvals),
        'incident_approvals': len(incident_approvals),
    }
    return render_template('admin.html', incidents=incidents, resources=resources, approvals=approvals, active_allocations=active_allocations, incident_approvals=incident_approvals, hospitals=hospitals, agents=agents, logs=logs, plans=plans, stats=stats, hospital_requests=hospital_requests, demo_admin_email=Config.DEMO_ADMIN_EMAIL, demo_admin_password=Config.DEMO_ADMIN_PASSWORD)


@app.get('/hospital')
@require_role('HOSPITAL_OPERATOR')
def hospital_module():
    hospital_id = request.operator['organization_id']
    resources = query('SELECT * FROM resources WHERE hospital_id=%s ORDER BY resource_type,id', (hospital_id,), True)
    capacity_rows = query('SELECT * FROM hospital_capacities WHERE hospital_id=%s', (hospital_id,), True)
    capacity = capacity_rows[0] if capacity_rows else {
        'general_beds': 0, 'icu_beds': 0, 'oxygen_beds': 0, 'trauma_beds': 0,
        'doctors': 0, 'nurses': 0, 'blood_units': 0
    }
    resource_summary = {}
    for rtype in ('AMBULANCE','MEDICAL_TEAM','RESCUE_TEAM','SHELTER'):
        units = [r for r in resources if r['resource_type'] == rtype]
        resource_summary[rtype] = {
            'total': len(units),
            'available': sum(1 for r in units if r['state'] == 'AVAILABLE'),
            'reserved': sum(1 for r in units if r['state'] == 'RESERVED'),
            'dispatched': sum(1 for r in units if r['state'] == 'DISPATCHED'),
        }
    requests = query('''SELECT hr.*, i.title,i.location_text,i.latitude,i.longitude,i.severity,i.urgency
                        FROM hospital_requests hr JOIN incidents i ON i.id=hr.incident_id
                        WHERE hr.hospital_id=%s ORDER BY hr.contacted_at DESC''', (hospital_id,), True)
    for q in requests:
        for key in ('requested_resources','response_data'):
            if isinstance(q.get(key),str):
                try:q[key]=json.loads(q[key])
                except Exception:pass
    allocations = query('''SELECT a.*,i.title,r.label,r.resource_type,r.latitude,r.longitude,r.state FROM allocations a JOIN incidents i ON i.id=a.incident_id JOIN resources r ON r.id=a.resource_id
                           WHERE r.hospital_id=%s AND a.status IN ('PROPOSED','RESERVED','DISPATCHED') ORDER BY a.created_at DESC''', (hospital_id,), True)
    notifications = query("SELECT * FROM notifications WHERE recipient_type='HOSPITAL' AND recipient_id=%s ORDER BY created_at DESC LIMIT 30",(hospital_id,),True)
    current_plan_rows = query('SELECT * FROM plans ORDER BY version DESC LIMIT 1', fetch=True)
    hospital_plan = current_plan_rows[0] if current_plan_rows else None
    hospital_plan_items = []
    if hospital_plan:
        hospital_plan_items = query('''SELECT pi.*,i.title,i.severity,i.location_text,i.latitude AS incident_latitude,i.longitude AS incident_longitude,r.label,r.resource_type,r.state,r.latitude,r.longitude
                                      FROM plan_items pi JOIN incidents i ON i.id=pi.incident_id LEFT JOIN resources r ON r.id=pi.resource_id
                                      WHERE pi.plan_id=%s AND r.hospital_id=%s ORDER BY pi.id''', (hospital_plan['id'], hospital_id), True)
    return render_template('hospital.html', resources=resources, requests=requests, allocations=allocations, notifications=notifications, capacity=capacity, resource_summary=resource_summary, hospital_plan=hospital_plan, hospital_plan_items=hospital_plan_items)


@app.get('/api/hospital/notifications')
@require_role('HOSPITAL_OPERATOR')
def hospital_notifications_api():
    hid=request.operator['organization_id']
    rows=query("SELECT * FROM notifications WHERE recipient_type='HOSPITAL' AND recipient_id=%s ORDER BY created_at DESC LIMIT 50",(hid,),True)
    unread=sum(1 for n in rows if not n.get('is_read'))
    return jsonify({'notifications':rows,'unread':unread})


@app.post('/api/hospital/capacity')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def update_hospital_capacity():
    hid = request.operator['organization_id']
    payload = request.get_json(silent=True) or {}
    keys = ('general_beds','icu_beds','oxygen_beds','trauma_beds','doctors','nurses','blood_units')
    values = {}
    try:
        for key in keys:
            value = int(payload.get(key, 0))
            if value < 0 or value > 10000:
                raise ValueError(f'Invalid value for {key}')
            values[key] = value

        query('''INSERT INTO hospital_capacities
                 (hospital_id,general_beds,icu_beds,oxygen_beds,trauma_beds,doctors,nurses,blood_units)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                 ON DUPLICATE KEY UPDATE general_beds=VALUES(general_beds),icu_beds=VALUES(icu_beds),
                 oxygen_beds=VALUES(oxygen_beds),trauma_beds=VALUES(trauma_beds),doctors=VALUES(doctors),
                 nurses=VALUES(nurses),blood_units=VALUES(blood_units)''',
              (hid, values['general_beds'], values['icu_beds'], values['oxygen_beds'],
               values['trauma_beds'], values['doctors'], values['nurses'], values['blood_units']))
        audit(request.operator['id'], 'HOSPITAL_CAPACITY_UPDATED', 'ORGANIZATION', hid, json.dumps(values))
        return jsonify({'status':'updated','capacity':values,'message':'Hospital capacity updated successfully.'})
    except (TypeError, ValueError) as exc:
        return jsonify({'error':str(exc)}), 400


@app.post('/api/hospital/resource-inventory')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def update_hospital_resource_inventory():
    hid = request.operator['organization_id']
    payload = request.get_json(silent=True) or {}
    labels = {'AMBULANCE':'Ambulance','MEDICAL_TEAM':'Medical Team','RESCUE_TEAM':'Rescue Team','SHELTER':'Shelter'}
    capabilities = {'AMBULANCE':['basic_transport','gps'], 'MEDICAL_TEAM':['first_response'], 'RESCUE_TEAM':['extrication'], 'SHELTER':['temporary_shelter']}
    try:
        counts = {}
        for rtype in labels:
            value = int(payload.get(rtype.lower(), 0))
            if value < 0 or value > 100:
                raise ValueError(f'Invalid quantity for {rtype}')
            counts[rtype] = value

        for rtype, target in counts.items():
            rows = query('SELECT id,state FROM resources WHERE hospital_id=%s AND resource_type=%s ORDER BY id', (hid,rtype), True)
            active = [r for r in rows if r['state'] in ('RESERVED','DISPATCHED','UNAVAILABLE','MAINTENANCE')]
            available = [r for r in rows if r['state']=='AVAILABLE']
            protected = len(active)
            if target < protected:
                raise ValueError(f'Cannot reduce {rtype} below {protected}; some units are reserved, dispatched or unavailable.')
            if target > len(rows):
                for idx in range(len(rows)+1, target+1):
                    query('''INSERT INTO resources (hospital_id,resource_type,label,state,capabilities,latitude,longitude)
                             SELECT %s,%s,%s,'AVAILABLE',%s,latitude,longitude FROM organizations WHERE id=%s''',
                          (hid,rtype,f"{labels[rtype]}-{idx}",json.dumps(capabilities[rtype]),hid))
            elif target < len(rows):
                remove_n = len(rows) - target
                for row in reversed(available[-remove_n:] if remove_n else []):
                    query("DELETE FROM resources WHERE id=%s AND hospital_id=%s AND state='AVAILABLE'", (row['id'],hid))

        audit(request.operator['id'], 'HOSPITAL_RESOURCE_INVENTORY_UPDATED', 'ORGANIZATION', hid, json.dumps(counts))
        return jsonify({'status':'updated','counts':counts,'message':'Emergency resource inventory updated. AI allocation will use the new availability.'})
    except (TypeError, ValueError) as exc:
        return jsonify({'error':str(exc)}), 400


@app.get('/api/dashboard')
@require_role('ADMIN','HOSPITAL_OPERATOR')
def dashboard_api():
    role = request.operator['role']
    if role == 'HOSPITAL_OPERATOR':
        hid = request.operator['organization_id']
        rows = query('SELECT * FROM resources WHERE hospital_id=%s', (hid,), True)
        capacity = query('SELECT * FROM hospital_capacities WHERE hospital_id=%s', (hid,), True)
        return jsonify({'module':'HOSPITAL','resources':rows,'capacity':capacity[0] if capacity else {}})
    return jsonify({'module':'ADMIN','incidents':query('SELECT * FROM incidents ORDER BY created_at DESC', fetch=True), 'resources':query('SELECT * FROM resources', fetch=True)})


@app.post('/api/auth/login')
def api_login():
    payload = request.get_json(silent=True) or {}
    rows = query('''SELECT u.*, o.verified AS hospital_verified FROM users u LEFT JOIN organizations o ON o.id=u.organization_id
                   WHERE u.email=%s AND u.active=TRUE AND (u.role <> 'HOSPITAL_OPERATOR' OR o.verified=TRUE)''', (str(payload.get('email','')).lower(),), True)
    if not rows or rows[0].get('role') not in ROLE_HOME or not verify_password(str(payload.get('password','')), rows[0]['password_hash']):
        return jsonify({'error':'Invalid credentials'}), 401
    user = rows[0]
    session.clear(); session['user_id']=user['id']; session['csrf_token']=secrets.token_urlsafe(32)
    return jsonify({'status':'ok','role':user['role'],'csrf_token':session['csrf_token']})


@app.get('/api/incidents')
@require_role('ADMIN','HOSPITAL_OPERATOR')
def list_incidents():
    return jsonify(query('SELECT * FROM incidents ORDER BY created_at DESC',fetch=True))


@app.post('/api/incidents')
@require_csrf
def api_create_incident():
    return user_create_incident()


@app.get('/api/incidents/<int:incident_id>')
@require_role('ADMIN','HOSPITAL_OPERATOR')
def incident_detail(incident_id):
    rows = query('SELECT * FROM incidents WHERE id=%s',(incident_id,),True)
    if not rows: return jsonify({'error':'Incident not found'}),404
    incident=rows[0]
    requests = query('SELECT hr.*,o.name AS hospital_name FROM hospital_requests hr JOIN organizations o ON o.id=hr.hospital_id WHERE hr.incident_id=%s ORDER BY hr.distance_km',(incident_id,),True)
    allocations = query('SELECT a.*,r.label,o.name AS hospital_name FROM allocations a JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id WHERE a.incident_id=%s',(incident_id,),True)
    return jsonify({'incident':incident,'hospital_requests':requests,'allocations':allocations})


@app.post('/api/incidents/<int:incident_id>/approve')
@require_role('ADMIN')
@require_csrf
def approve_incident(incident_id):
    rows = query('SELECT * FROM incidents WHERE id=%s', (incident_id,), True)
    if not rows: return jsonify({'error':'Incident not found'}), 404
    incident = rows[0]
    if incident.get('human_decision') == 'REJECTED':
        return jsonify({'error':'This incident was already rejected'}), 409
    query("UPDATE incidents SET human_decision='APPROVED', human_decision_by=%s, human_decision_at=NOW(), status='UNDER_ASSESSMENT' WHERE id=%s", (request.operator['id'], incident_id))
    needs = incident_needs(incident)
    assessment = IncidentAssessmentAgent().assess({**incident, 'resource_needs': needs})
    query("UPDATE incidents SET assessment_json=%s,confidence=%s,status='COORDINATING' WHERE id=%s", (json.dumps(assessment), assessment['confidence'], incident_id))
    log_agent('incident_assessment', {'incident_id': incident_id, 'human_approved': True}, assessment)
    network = create_hospital_requests(incident_id, float(incident['latitude']), float(incident['longitude']), needs, request.operator['id'])
    audit(request.operator['id'], 'INCIDENT_APPROVED', 'INCIDENT', incident_id, json.dumps({'resource_needs': needs, 'hospital_ids': [h['id'] for h in network['hospitals']]}))
    return jsonify({'status':'approved','incident_id':incident_id,'resource_needs':needs,'hospitals_contacted':len(network['hospitals'])})


@app.post('/api/incidents/<int:incident_id>/reject')
@require_role('ADMIN')
@require_csrf
def reject_incident(incident_id):
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason','')).strip()
    if not reason: return jsonify({'error':'Rejection reason is required'}), 400
    rows = query('SELECT * FROM incidents WHERE id=%s', (incident_id,), True)
    if not rows: return jsonify({'error':'Incident not found'}), 404
    query("UPDATE incidents SET human_decision='REJECTED', human_decision_by=%s, human_decision_at=NOW(), human_decision_reason=%s WHERE id=%s", (request.operator['id'], reason, incident_id))
    audit(request.operator['id'], 'INCIDENT_REJECTED', 'INCIDENT', incident_id, json.dumps({'reason':reason}))
    return jsonify({'status':'rejected','incident_id':incident_id})


@app.post('/api/incidents/<int:incident_id>/assess')
@require_role('ADMIN')
@require_csrf
def assess_incident(incident_id):
    rows=query('SELECT * FROM incidents WHERE id=%s',(incident_id,),True)
    if not rows:return jsonify({'error':'Incident not found'}),404
    incident=rows[0]
    needs=incident_needs(incident)
    result=IncidentAssessmentAgent().assess({**incident,'resource_needs':needs})
    query("UPDATE incidents SET assessment_json=%s,confidence=%s,status='UNDER_ASSESSMENT' WHERE id=%s",(json.dumps(result),result['confidence'],incident_id))
    log_agent('incident_assessment',{'incident_id':incident_id},result)
    audit(request.operator['id'],'INCIDENT_ASSESSED','INCIDENT',incident_id,json.dumps(result))
    return jsonify(result)


@app.get('/api/hospitals/nearby')
@require_role('ADMIN')
def nearby_hospitals():
    try:
        lat,lon=validate_coordinates(request.args.get('lat'),request.args.get('lon'))
        radius=float(request.args.get('radius',10))
    except (TypeError,ValueError) as exc:return jsonify({'error':str(exc)}),400
    all_hospitals=HospitalCoordinationAgent().find_nearby(lat,lon,{'AMBULANCE':1},initial_radius_km=radius,max_radius_km=radius,step_km=radius)
    return jsonify(all_hospitals)


@app.get('/api/resources')
@require_role('ADMIN','HOSPITAL_OPERATOR')
def resources_api():
    hid = request.operator['organization_id'] if request.operator['role']=='HOSPITAL_OPERATOR' else None
    if hid:
        return jsonify(query('SELECT * FROM resources WHERE hospital_id=%s ORDER BY id', (hid,), True))
    return jsonify(query('SELECT * FROM resources ORDER BY id', fetch=True))


@app.post('/api/resources/<int:resource_id>/status')
@require_role('ADMIN','HOSPITAL_OPERATOR')
@require_csrf
def resource_status(resource_id):
    payload=request.get_json(silent=True) or {}
    state=str(payload.get('state','')).upper()
    hid=request.operator['organization_id'] if request.operator['role']=='HOSPITAL_OPERATOR' else None
    try: changed=set_resource_state(resource_id,state,hid)
    except ValueError as exc:return jsonify({'error':str(exc)}),400
    if not changed:return jsonify({'error':'Resource not found or not owned by your hospital'}),404
    audit(request.operator['id'],'RESOURCE_STATUS_CHANGED','RESOURCE',resource_id,json.dumps({'state':state}))
    replan=None
    if state in {'UNAVAILABLE','MAINTENANCE'}:
        replan=run_replan(request.operator['id'],trigger=f'RESOURCE_{resource_id}_{state}')
    return jsonify({'status':'updated','resource_id':resource_id,'state':state,'replan':replan})


@app.post('/api/agents/run')
@require_role('ADMIN')
@require_csrf
def run_agents():
    payload=request.get_json(silent=True) or {}
    incident_id=payload.get('incident_id')
    agent=payload.get('agent','workflow')
    if agent=='workflow' and incident_id:
        return coordinate_incident(incident_id)
    if agent=='replanning':
        return jsonify(run_replan(request.operator['id'],'MANUAL'))
    return jsonify({'error':'Supported agents: workflow, replanning'}),400


def coordinate_incident(incident_id):
    rows=query('SELECT * FROM incidents WHERE id=%s',(incident_id,),True)
    if not rows:return jsonify({'error':'Incident not found'}),404
    incident=rows[0]
    if incident.get('human_decision') != 'APPROVED':
        return jsonify({'error':'Human approval is required before hospital requests and AI allocation can run for this incident.'}), 409
    needs=incident_needs(incident)
    assessment=IncidentAssessmentAgent().assess({**incident,'resource_needs':needs})
    query("UPDATE incidents SET assessment_json=%s,confidence=%s,status='COORDINATING' WHERE id=%s",(json.dumps(assessment),assessment['confidence'],incident_id))
    log_agent('incident_assessment',{'incident_id':incident_id},assessment)
    existing_requests = query('''SELECT hr.*,o.name AS hospital_name FROM hospital_requests hr JOIN organizations o ON o.id=hr.hospital_id
                                 WHERE hr.incident_id=%s ORDER BY hr.distance_km''',(incident_id,),True)
    if not existing_requests:
        network=create_hospital_requests(incident_id,float(incident['latitude']),float(incident['longitude']),needs,request.operator['id'])
        existing_requests = query('''SELECT hr.*,o.name AS hospital_name FROM hospital_requests hr JOIN organizations o ON o.id=hr.hospital_id
                                     WHERE hr.incident_id=%s ORDER BY hr.distance_km''',(incident_id,),True)
    else:
        network={'hospitals':[{'id':r['hospital_id'],'name':r['hospital_name'],'distance_km':r['distance_km']} for r in existing_requests], 'expanded':False, 'radius_km':None}
    confirmed = [r for r in existing_requests if r['response_status'] in ('APPROVED','PARTIAL')]
    eligible_resource_ids=[]
    confirmed_hospital_ids=[]
    for r in confirmed:
        data=r.get('response_data') or {}
        if isinstance(data,str):
            try: data=json.loads(data)
            except Exception: data={}
        eligible_resource_ids.extend(data.get('resource_ids') or [])
        confirmed_hospital_ids.append(r['hospital_id'])
    eligible_resource_ids=sorted(set(int(x) for x in eligible_resource_ids if str(x).isdigit()))
    if not confirmed:
        return jsonify({'assessment':assessment,'network':network,'proposal':{'ok':False,'pending_hospital_responses':len(existing_requests),'message':'Waiting for nearby hospitals to accept or decline the request and specify available resources.'}})
    proposal=ResourceAllocationAgent().propose(incident,needs,confirmed_hospital_ids,eligible_resource_ids)
    log_agent('resource_allocation',{'incident_id':incident_id,'hospital_ids':confirmed_hospital_ids,'eligible_resource_ids':eligible_resource_ids},proposal)
    if not proposal['ok']:
        query("UPDATE incidents SET status='COORDINATING' WHERE id=%s",(incident_id,))
        audit(request.operator['id'],'ALLOCATION_SHORTAGE','INCIDENT',incident_id,json.dumps(proposal))
        return jsonify({'assessment':assessment,'network':network,'proposal':proposal})
    proposal_id=str(uuid.uuid4())
    selected_ids=[]
    for r in proposal['selected']:
        selected_ids.append(r['id'])
        ai_reason = '; '.join(r.get('ai_reasons', []))
        reason=f"AI Resource Allocation selected {r['label']} from {r['hospital_name']} because it is currently AVAILABLE and the hospital is eligible by verified proximity. AI score: {r.get('ai_score', 0):.4f}. Factors: {ai_reason}."
        query('''INSERT INTO allocations (incident_id,resource_id,proposal_id,status,reason,priority_basis,expected_state) VALUES (%s,%s,%s,'PROPOSED',%s,%s,'RESERVED')''',
              (incident_id,r['id'],proposal_id,reason,f"Severity {incident['severity']}; urgency {incident['urgency']}/10"))
    route_plan = RouteLogisticsAgent().plan(incident, proposal['selected'])
    log_agent('route_logistics', {'incident_id': incident_id, 'resource_ids': selected_ids}, route_plan)
    explanation = CommandPlanningAgent().build(incident, proposal['selected'], route_plan)
    log_agent('command_planning', {'incident_id': incident_id, 'proposal_id': proposal_id}, explanation)
    query("UPDATE incidents SET status='AWAITING_APPROVAL' WHERE id=%s",(incident_id,))
    audit(request.operator['id'],'ALLOCATION_PROPOSED','INCIDENT',incident_id,json.dumps({'proposal_id':proposal_id,'resource_ids':selected_ids}))
    return jsonify({'assessment':assessment,'network':network,'route_logistics':route_plan,
                    'proposal':{**proposal,'proposal_id':proposal_id,'explanation':explanation}})


@app.post('/api/allocations/propose')
@require_role('ADMIN')
@require_csrf
def propose_allocation():
    payload=request.get_json(silent=True) or {}
    if not payload.get('incident_id'):return jsonify({'error':'incident_id required'}),400
    return coordinate_incident(int(payload['incident_id']))


@app.post('/api/allocations/<int:allocation_id>/approve')
@require_role('ADMIN')
@require_csrf
def approve_allocation(allocation_id):
    conn,cur=transaction()
    try:
        cur.execute('''SELECT a.*,r.state,r.hospital_id,r.resource_type,r.label AS resource_label,o.name AS hospital_name,i.title AS incident_title,i.location_text,i.latitude,i.longitude FROM allocations a JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id JOIN incidents i ON i.id=a.incident_id WHERE a.id=%s FOR UPDATE''',(allocation_id,))
        row=cur.fetchone()
        if not row:return jsonify({'error':'Allocation not found'}),404
        if row['status']!='PROPOSED':return jsonify({'error':'Allocation is not awaiting approval'}),409
        if row['state']!='AVAILABLE':
            conn.rollback(); run_replan(request.operator['id'],f'APPROVAL_CONFLICT_{allocation_id}')
            return jsonify({'error':'Resource is no longer available; allocation was not made and replanning was triggered'}),409
        cur.execute("UPDATE resources SET state='RESERVED' WHERE id=%s AND state='AVAILABLE'",(row['resource_id'],))
        if cur.rowcount!=1:
            conn.rollback(); return jsonify({'error':'Resource changed concurrently; replan required'}),409
        cur.execute("UPDATE allocations SET status='RESERVED',approved_by=%s,approved_at=NOW() WHERE id=%s",(request.operator['id'],allocation_id))
        cur.execute("UPDATE incidents SET status='APPROVED_DISPATCHED' WHERE id=%s",(row['incident_id'],))
        cur.execute("UPDATE hospital_requests SET response_status='APPROVED',responded_at=NOW() WHERE incident_id=%s AND hospital_id=%s",(row['incident_id'],row['hospital_id']))
        cur.execute('''INSERT INTO notifications (recipient_type,recipient_id,incident_id,allocation_id,priority,title,message)
                       VALUES ('HOSPITAL',%s,%s,%s,%s,%s,%s)''',
                    (row['hospital_id'],row['incident_id'],allocation_id,
                     'CRITICAL' if 'CRITICAL' in row.get('priority_basis','') else ('HIGH' if 'HIGH' in row.get('priority_basis','') else 'NORMAL'),
                     'Allocation approved — prepare dispatch',
                     'Incident #%s is %s. Dispatch %s (%s) from %s to %s. Destination coordinates: %s, %s. This resource was selected from the hospital-confirmed availability. Please prepare the resource and confirm dispatch in the hospital module.' % (row['incident_id'], 'CRITICAL' if 'CRITICAL' in row.get('priority_basis','') else ('HIGH' if 'HIGH' in row.get('priority_basis','') else 'NORMAL'), row.get('resource_label') or ('Resource #' + str(row.get('resource_id'))), row.get('resource_type','RESOURCE'), row.get('hospital_name','your hospital'), row.get('location_text','incident location'), row.get('latitude','—'), row.get('longitude','—'))) )
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close();conn.close()
    audit(request.operator['id'],'ALLOCATION_APPROVED','ALLOCATION',allocation_id,json.dumps({'resource_id':row['resource_id']}))
    query('''INSERT INTO notifications (recipient_type,recipient_id,incident_id,allocation_id,priority,title,message)
              VALUES ('USER',NULL,%s,%s,%s,%s,%s)''',
          (row['incident_id'],allocation_id,
           'CRITICAL' if 'CRITICAL' in row.get('priority_basis','') else ('HIGH' if 'HIGH' in row.get('priority_basis','') else 'NORMAL'),
           'Resource allocated',
           'A resource has been approved for your emergency. Use Track Your Report to view the latest allocation and location.'))
    return jsonify({'status':'approved','allocation_id':allocation_id})


@app.post('/api/allocations/<int:allocation_id>/reject')
@require_role('ADMIN')
@require_csrf
def reject_allocation(allocation_id):
    payload=request.get_json(silent=True) or {}
    reason=str(payload.get('reason','')).strip()
    if not reason:return jsonify({'error':'Rejection reason is required'}),400
    changed=query("UPDATE allocations SET status='CANCELLED',reason=CONCAT(reason,' | Rejected: ',%s) WHERE id=%s AND status='PROPOSED'",(reason,allocation_id))
    if changed==0:return jsonify({'error':'Allocation not found or already processed'}),409
    audit(request.operator['id'],'ALLOCATION_REJECTED','ALLOCATION',allocation_id,json.dumps({'reason':reason}))
    return jsonify({'status':'rejected','allocation_id':allocation_id})


@app.post('/api/replan')
@require_role('ADMIN')
@require_csrf
def api_replan():
    return jsonify(run_replan(request.operator['id'],'MANUAL'))


@app.get('/api/plans/current')
@require_role('ADMIN')
def current_plan():
    rows=query('SELECT * FROM plans ORDER BY version DESC LIMIT 1',fetch=True)
    if not rows:return jsonify({'plan':None})
    plan=rows[0]
    plan['items']=query('SELECT * FROM plan_items WHERE plan_id=%s ORDER BY id',(plan['id'],),True)
    return jsonify(plan)


@app.get('/api/plans/<int:plan_id>/compare')
@require_role('ADMIN')
def compare_plan(plan_id):
    rows=query('SELECT * FROM plans WHERE id=%s',(plan_id,),True)
    if not rows:return jsonify({'error':'Plan not found'}),404
    current=rows[0]
    previous=query('SELECT * FROM plans WHERE version < %s ORDER BY version DESC LIMIT 1',(current['version'],),True)
    return jsonify({'current':current,'previous':previous[0] if previous else None,'current_items':query('SELECT * FROM plan_items WHERE plan_id=%s',(plan_id,),True),'previous_items':query('SELECT * FROM plan_items WHERE plan_id=%s',(previous[0]['id'],),True) if previous else []})


@app.get('/api/agent-runs')
@require_role('ADMIN')
def agent_runs():
    return jsonify(query('SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 100',fetch=True))


@app.get('/api/audit-logs')
@require_role('ADMIN')
def audit_logs():
    return jsonify(query('SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200',fetch=True))


@app.post('/api/hospitals/<int:hospital_id>/requests/<int:request_id>/respond')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def respond_hospital_request(hospital_id, request_id):
    if request.operator['organization_id'] != hospital_id: return jsonify({'error':'Forbidden'}),403
    payload=request.get_json(silent=True) or {}
    decision=str(payload.get('decision','')).upper()
    if decision not in {'ACCEPT','REJECT'}: return jsonify({'error':'Decision must be ACCEPT or REJECT'}),400
    rows=query('''SELECT hr.*,i.title,i.severity,i.required_resources FROM hospital_requests hr JOIN incidents i ON i.id=hr.incident_id
                  WHERE hr.id=%s AND hr.hospital_id=%s''',(request_id,hospital_id),True)
    if not rows:return jsonify({'error':'Request not found'}),404
    req=rows[0]
    if decision=='REJECT':
        query("UPDATE hospital_requests SET response_status='DECLINED',response_data=%s,responded_at=NOW() WHERE id=%s",(json.dumps({'reason':str(payload.get('reason','Hospital declined the request.'))}),request_id))
        audit(request.operator['id'],'HOSPITAL_REQUEST_DECLINED','HOSPITAL_REQUEST',request_id,json.dumps({'reason':payload.get('reason','')}))
        return jsonify({'status':'declined'})
    requested=req.get('requested_resources') or req.get('required_resources') or {}
    if isinstance(requested,str): requested=json.loads(requested)
    offered_payload=payload.get('resources') or {}
    offered={}
    resource_ids=[]
    for rtype,needed in requested.items():
        try: amount=int(offered_payload.get(rtype,0))
        except Exception: return jsonify({'error':f'Invalid quantity for {rtype}'}),400
        if amount<0 or amount>int(needed):return jsonify({'error':f'{rtype} must be between 0 and {needed}'}),400
        available=query("SELECT id FROM resources WHERE hospital_id=%s AND resource_type=%s AND state='AVAILABLE' ORDER BY id LIMIT %s",(hospital_id,rtype,amount),True) if amount else []
        if len(available)<amount:return jsonify({'error':f'Only {len(available)} {rtype} currently available'}),409
        offered[rtype]=amount
        resource_ids.extend(r['id'] for r in available)
    if not resource_ids:return jsonify({'error':'Specify at least one available resource or reject the request.'}),400
    full=all(int(offered.get(k,0))==int(v) for k,v in requested.items())
    status='APPROVED' if full else 'PARTIAL'
    data={'offered_resources':offered,'resource_ids':resource_ids,'message':'Hospital confirmed the resources it can provide.'}
    query("UPDATE hospital_requests SET response_status=%s,response_data=%s,responded_at=NOW() WHERE id=%s",(status,json.dumps(data),request_id))
    audit(request.operator['id'],'HOSPITAL_REQUEST_ACCEPTED' if full else 'HOSPITAL_REQUEST_PARTIAL','HOSPITAL_REQUEST',request_id,json.dumps(data))
    return jsonify({'status':status.lower(),'offered_resources':offered,'resource_ids':resource_ids})


@app.post('/api/hospitals/<int:hospital_id>/requests/<int:request_id>/ack')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def acknowledge_request(hospital_id,request_id):
    if request.operator['organization_id']!=hospital_id:return jsonify({'error':'Forbidden'}),403
    changed=query("UPDATE hospital_requests SET response_status='ACKNOWLEDGED',responded_at=NOW() WHERE id=%s AND hospital_id=%s AND response_status IN ('CONTACTED','APPROVED','PARTIAL')",(request_id,hospital_id))
    if changed==0:return jsonify({'error':'Request not found or already handled'}),409
    audit(request.operator['id'],'HOSPITAL_REQUEST_ACKNOWLEDGED','HOSPITAL_REQUEST',request_id,'{}')
    return jsonify({'status':'acknowledged'})


@app.post('/api/hospitals/<int:hospital_id>/resources/<int:resource_id>/location')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def update_resource_location(hospital_id, resource_id):
    if request.operator['organization_id'] != hospital_id: return jsonify({'error':'Forbidden'}),403
    payload=request.get_json(silent=True) or {}
    try: lat,lon=validate_coordinates(payload.get('latitude'),payload.get('longitude'))
    except (TypeError,ValueError) as exc:return jsonify({'error':str(exc)}),400
    changed=query('UPDATE resources SET latitude=%s,longitude=%s WHERE id=%s AND hospital_id=%s',(lat,lon,resource_id,hospital_id))
    if not changed:return jsonify({'error':'Resource not found'}),404
    audit(request.operator['id'],'RESOURCE_LOCATION_UPDATED','RESOURCE',resource_id,json.dumps({'latitude':lat,'longitude':lon}))
    return jsonify({'status':'updated','resource_id':resource_id,'latitude':lat,'longitude':lon})


@app.post('/api/hospitals/<int:hospital_id>/allocations/<int:allocation_id>/dispatch')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def dispatch_allocation(hospital_id,allocation_id):
    if request.operator['organization_id']!=hospital_id:return jsonify({'error':'Forbidden'}),403
    conn,cur=transaction()
    try:
        cur.execute('''SELECT a.*,r.state,r.hospital_id,r.resource_type,r.label AS resource_label,o.name AS hospital_name,i.title AS incident_title,i.location_text,i.latitude,i.longitude FROM allocations a JOIN resources r ON r.id=a.resource_id JOIN organizations o ON o.id=r.hospital_id JOIN incidents i ON i.id=a.incident_id WHERE a.id=%s FOR UPDATE''',(allocation_id,))
        row=cur.fetchone()
        if not row or row['hospital_id']!=hospital_id:return jsonify({'error':'Allocation not found'}),404
        if row['status']!='RESERVED' or row['state']!='RESERVED':return jsonify({'error':'Resource must be RESERVED before dispatch'}),409
        cur.execute("UPDATE resources SET state='DISPATCHED' WHERE id=%s AND state='RESERVED'",(row['resource_id'],))
        if cur.rowcount!=1:conn.rollback();return jsonify({'error':'Resource state changed; dispatch stopped'}),409
        cur.execute("UPDATE allocations SET status='DISPATCHED' WHERE id=%s",(allocation_id,))
        cur.execute("UPDATE hospital_requests SET response_status='FULFILLED',responded_at=NOW() WHERE incident_id=%s AND hospital_id=%s",(row['incident_id'],hospital_id))
        cur.execute("UPDATE incidents SET status='IN_PROGRESS' WHERE id=%s",(row['incident_id'],))
        conn.commit()
    except Exception:
        conn.rollback();raise
    finally:
        cur.close();conn.close()
    audit(request.operator['id'],'RESOURCE_DISPATCHED','ALLOCATION',allocation_id,json.dumps({'resource_id':row['resource_id']}))
    return jsonify({'status':'dispatched','allocation_id':allocation_id})


@app.post('/api/hospitals/<int:hospital_id>/allocations/<int:allocation_id>/complete')
@require_role('HOSPITAL_OPERATOR')
@require_csrf
def complete_allocation(hospital_id,allocation_id):
    if request.operator['organization_id']!=hospital_id:return jsonify({'error':'Forbidden'}),403
    conn,cur=transaction()
    try:
        cur.execute('''SELECT a.*,r.state,r.hospital_id,r.label AS resource_label,i.id AS incident_id,i.title AS incident_title FROM allocations a JOIN resources r ON r.id=a.resource_id JOIN incidents i ON i.id=a.incident_id WHERE a.id=%s FOR UPDATE''',(allocation_id,))
        row=cur.fetchone()
        if not row or row['hospital_id']!=hospital_id:return jsonify({'error':'Allocation not found'}),404
        if row['status']!='DISPATCHED' or row['state']!='DISPATCHED':return jsonify({'error':'Resource must be DISPATCHED before completion'}),409
        cur.execute("UPDATE resources SET state='AVAILABLE' WHERE id=%s AND state='DISPATCHED'",(row['resource_id'],))
        cur.execute("UPDATE allocations SET status='COMPLETED' WHERE id=%s",(allocation_id,))
        cur.execute("UPDATE hospital_requests SET response_status='COMPLETED',responded_at=NOW() WHERE incident_id=%s AND hospital_id=%s",(row['incident_id'],hospital_id))
        cur.execute("SELECT COUNT(*) AS n FROM allocations WHERE incident_id=%s AND status IN ('PROPOSED','RESERVED','DISPATCHED')",(row['incident_id'],))
        remaining=int(cur.fetchone()['n'])
        if remaining==0: cur.execute("UPDATE incidents SET status='RESOLVED' WHERE id=%s",(row['incident_id'],))
        conn.commit()
    except Exception:
        conn.rollback();raise
    finally:
        cur.close();conn.close()
    audit(request.operator['id'],'RESOURCE_OPERATION_COMPLETED','ALLOCATION',allocation_id,json.dumps({'resource_id':row['resource_id'],'incident_id':row['incident_id'],'incident_status':'RESOLVED' if remaining==0 else 'IN_PROGRESS'}))
    return jsonify({'status':'completed','allocation_id':allocation_id,'incident_resolved':remaining==0})

@app.post('/api/demo/reset')
@require_role('ADMIN')
@require_csrf
def reset_demo():
    conn=get_connection();cur=conn.cursor()
    try:
        cur.execute('DELETE FROM plan_items');cur.execute('DELETE FROM plans');cur.execute('DELETE FROM allocations');cur.execute('DELETE FROM hospital_requests');cur.execute('DELETE FROM agent_runs');cur.execute('DELETE FROM incidents');cur.execute("UPDATE resources SET state='AVAILABLE'")
        conn.commit()
    except Exception:conn.rollback();raise
    finally:cur.close();conn.close()
    audit(request.operator['id'],'DEMO_RESET','SYSTEM',None,'{}')
    return jsonify({'status':'reset'})


