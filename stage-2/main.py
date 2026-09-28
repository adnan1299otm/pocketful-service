from flask import Flask, jsonify, request, abort
import uuid
import hashlib
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Enum, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.exc import IntegrityError

Base = declarative_base()
engine = create_engine('sqlite:///:memory:')
Session = sessionmaker(bind=engine)
session = Session()

app = Flask(__name__)

# Define Enums
class Visibility(Enum):
    public = 'public'
    private = 'private'

class Status(Enum):
    pending = 'pending'
    paid = 'paid'
    cancelled = 'cancelled'

class ActionType(Enum):
    payment_sent = 'payment_sent'
    payment_received = 'payment_received'
    request_created = 'request_created'
    request_paid = 'request_paid'
    request_declined = 'request_declined'
    request_cancelled = 'request_cancelled'

# Define Models

class User(Base):
    __tablename__ = 'users'
    user_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name = Column(String, nullable=False)
    handle = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    balance = Column(Integer, default=0, nullable=False)
    currency = Column(String, nullable=False)
    minor_units = Column(Integer, nullable=False)


class Payment(Base):
    __tablename__ = 'payments'
    payment_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sender_user_id = Column(String, ForeignKey('users.user_id'), nullable=False)
    receiver_handle = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    note = Column(Text)
    visibility = Column(Enum(Visibility), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class PaymentRequest(Base):
    __tablename__ = 'payment_requests'
    request_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    requester_user_id = Column(String, ForeignKey('users.user_id'), nullable=False)
    payer_user_id = Column(String, ForeignKey('users.user_id'))
    amount = Column(Integer, nullable=False)
    status = Column(Enum(Status), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class ActivityFeed(Base):
    __tablename__ = 'activity_feed'
    activity_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.user_id'), nullable=False)
    payment_id = Column(String, ForeignKey('payments.payment_id'))
    request_id = Column(String, ForeignKey('payment_requests.request_id'))
    action_type = Column(Enum(ActionType), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class IdempotencyKey(Base):
    __tablename__ = 'idempotency_keys'
    idempotency_key = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.user_id'), nullable=False)
    endpoint_path = Column(String, nullable=False)
    response_body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# Create Tables
Base.metadata.create_all(engine)

# Utility Functions

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_or_create(session, model, defaults=None, **kwargs):
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False
    else:
        params = {**kwargs, **defaults}
        instance = model(**params)
        try:
            session.add(instance)
            session.commit()
            return instance, True
        except IntegrityError:
            session.rollback()
            return session.query(model).filter_by(**kwargs).one(), False

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'}), 200

@app.route('/_test/reset', methods=['POST'])
def test_reset():
    meta = Base.metadata
    for table in reversed(meta.sorted_tables):
        engine.execute(table.delete())
    return '', 204

@app.route('/auth/signup', methods=['POST'])
def signup():
    data = request.json
    handle = data['email'].split('@')[0]
    hashed_password = hash_password(data['password'])
    new_user = User(
        display_name=data['display_name'],
        handle=handle,
        password_hash=hashed_password,
        currency='USD',
        minor_units=100
    )
    try:
        session.add(new_user)
        session.commit()
    except IntegrityError:
        session.rollback()
        abort(400, description="User already exists")
    token = str(uuid.uuid4())
    return jsonify({
        'user_id': new_user.user_id,
        'display_name': new_user.display_name,
        'token': token
    }), 201

@app.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    user = session.query(User).filter_by(handle=data['email'].split('@')[0]).first()
    if user and user.password_hash == hash_password(data['password']):
        token = str(uuid.uuid4())
        return jsonify({
            'user_id': user.user_id,
            'display_name': user.display_name,
            'token': token
        }), 200
    else:
        abort(401, description="Invalid credentials")

@app.route('/me', methods=['GET'])
def me():
    auth_token = request.headers.get('Authorization')
    # Dummy token validation; replace with real authentication mechanism
    user_id = auth_token.split()[1] if auth_token else None
    user = session.query(User).get(user_id)
    if not user:
        abort(401, description="Unauthorized")
    return jsonify({
        'user_id': user.user_id,
        'display_name': user.display_name,
        'handle': user.handle,
        'balance': user.balance,
        'currency': user.currency,
        'minor_units': user.minor_units
    }), 200

@app.route('/payments', methods=['POST'])
def create_payment():
    data = request.json
    sender = session.query(User).get(data['sender_user_id'])
    if not sender or sender.balance < data['amount']:
        abort(400, description="Insufficient funds or invalid sender")
    receiver_handle = data['receiver_handle']
    new_payment = Payment(
        sender_user_id=data['sender_user_id'],
        receiver_handle=receiver_handle,
        amount=data['amount'],
        note=data.get('note'),
        visibility=Visibility[data['visibility']],
        timestamp=datetime.utcnow()
    )
    session.add(new_payment)
    sender.balance -= data['amount']
    session.commit()
    # Add to activity feed
    activity = ActivityFeed(
        user_id=new_payment.sender_user_id,
        payment_id=new_payment.payment_id,
        action_type=ActionType.payment_sent,
        timestamp=datetime.utcnow()
    )
    session.add(activity)
    session.commit()
    return jsonify(vars(new_payment)), 201

@app.route('/requests', methods=['POST'])
def create_request():
    data = request.json
    requester = session.query(User).get(data['requester_user_id'])
    if not requester:
        abort(400, description="Invalid requester")
    new_request = PaymentRequest(
        requester_user_id=data['requester_user_id'],
        payer_user_id=data.get('payer_user_id'),
        amount=data['amount'],
        status=Status.pending,
        timestamp=datetime.utcnow()
    )
    session.add(new_request)
    session.commit()
    # Add to activity feed
    activity = ActivityFeed(
        user_id=new_request.requester_user_id,
        request_id=new_request.request_id,
        action_type=ActionType.request_created,
        timestamp=datetime.utcnow()
    )
    session.add(activity)
    session.commit()
    return jsonify(vars(new_request)), 201

@app.route('/requests/<request_id>/pay', methods=['POST'])
def pay_request(request_id):
    data = request.json
    payer = session.query(User).get(data['payer_user_id'])
    payment_request = session.query(PaymentRequest).get(request_id)
    if not payer or not payment_request or payment_request.status != Status.pending or payer.balance < payment_request.amount:
        abort(400, description="Invalid operation")
    payment_request.payer_user_id = data['payer_user_id']
    payment_request.status = Status.paid
    payer.balance -= payment_request.amount
    session.commit()
    # Add to activity feed
    activity = ActivityFeed(
        user_id=payment_request.payer_user_id,
        request_id=payment_request.request_id,
        action_type=ActionType.request_paid,
        timestamp=datetime.utcnow()
    )
    session.add(activity)
    session.commit()
    return jsonify(vars(payment_request)), 201

@app.route('/requests/<request_id>/decline', methods=['POST'])
def decline_request(request_id):
    payment_request = session.query(PaymentRequest).get(request_id)
    if not payment_request or payment_request.status != Status.pending:
        abort(400, description="Invalid operation")
    payment_request.status = Status.cancelled
    session.commit()
    # Add to activity feed
    activity = ActivityFeed(
        user_id=payment_request.requester_user_id,
        request_id=payment_request.request_id,
        action_type=ActionType.request_declined,
        timestamp=datetime.utcnow()
    )
    session.add(activity)
    session.commit()
    return jsonify(vars(payment_request)), 200

@app.route('/requests/<request_id>/cancel', methods=['POST'])
def cancel_request(request_id):
    payment_request = session.query(PaymentRequest).get(request_id)
    if not payment_request or payment_request.status != Status.pending:
        abort(400, description="Invalid operation")
    payment_request.status = Status.cancelled
    session.commit()
    # Add to activity feed
    activity = ActivityFeed(
        user_id=payment_request.requester_user_id,
        request_id=payment_request.request_id,
        action_type=ActionType.request_cancelled,
        timestamp=datetime.utcnow()
    )
    session.add(activity)
    session.commit()
    return jsonify(vars(payment_request)), 200

@app.route('/requests', methods=['GET'])
def list_requests():
    requests = session.query(PaymentRequest).all()
    return jsonify({
        'requests': [vars(req) for req in requests],
        'has_more': False
    }), 200

@app.route('/splits', methods=['POST'])
def create_split():
    # Placeholder for split creation logic
    return '', 201

@app.route('/activity', methods=['GET'])
def get_activity():
    activities = session.query(ActivityFeed).all()
    payments = []
    for activity in activities:
        if activity.action_type.name.startswith('payment'):
            payment = session.query(Payment).get(activity.payment_id)
            payments.append(vars(payment))
    return jsonify({
        'payments': payments,
        'has_more': False
    }), 200

@app.route('/settlements', methods=['POST'])
def create_settlement():
    # Placeholder for settlement creation logic
    return '', 201

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(app.config.get('PORT', 8080)))
