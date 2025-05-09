from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import time
import threading
from ping3 import ping, errors
import matplotlib
from config import WEBSITE_TARGETS, DRONE_TARGETS, CONFIG
from dataclasses import dataclass
import json
import zmq
from threading import Thread

matplotlib.use('Agg')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")


@dataclass
class PingResult:
    time: float
    latency: float
    status: str


# Data storage
website_data = {target: [] for target in WEBSITE_TARGETS}
drone_data = {target: [] for target in DRONE_TARGETS}
data_lock = threading.Lock()
start_time = time.time()


class PingListener(Thread):
    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio
        self.running = True
        self.context = zmq.Context()

    def run(self):
        socket = self.context.socket(zmq.SUB)
        socket.connect("tcp://localhost:5556")
        socket.setsockopt_string(zmq.SUBSCRIBE, "Ping")

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        while self.running:
            socks = dict(poller.poll(timeout=100))
            if socket in socks:
                try:
                    message = socket.recv_string()
                    if message.startswith("Ping "):
                        data = json.loads(message[5:])
                        if data.get('type') == 'drone_ping':
                            self.process_drone_ping(data)
                except Exception as e:
                    print(f"Error processing message: {e}")

    def process_drone_ping(self, data):
        current_time = time.time() - start_time
        updates = {}

        for target, result in data['results'].items():
            if target not in DRONE_TARGETS:
                continue

            try:
                latency = float(result['latency'])
                status = result.get('status', 'ok')

                if status not in ['ok', 'timeout', 'error']:
                    status = 'timeout' if latency >= CONFIG['DRONE_TIMEOUT'] else 'ok'

                updates[target] = {
                    'time': current_time,
                    'latency': latency,
                    'status': status
                }

                with data_lock:
                    if target not in drone_data:
                        drone_data[target] = []
                    drone_data[target].append(PingResult(
                        time=current_time,
                        latency=latency,
                        status=status
                    ))
                    if len(drone_data[target]) > CONFIG['MAX_HISTORY']:
                        drone_data[target].pop(0)

            except (ValueError, KeyError) as e:
                print(f"Error processing {target} data: {e}")
                continue

        if updates:
            self.socketio.emit('drone_update', updates)

    def stop(self):
        self.running = False
        self.context.destroy()


def ping_target(target):
    try:
        latency = ping(target, timeout=CONFIG['WEBSITE_PING_TIMEOUT'], unit='ms')
        if latency is None:
            return {'latency': CONFIG['TIMEOUT_THRESHOLD'], 'status': 'timeout'}
        if latency > CONFIG['LATENCY_TIMEOUT']:
            return {'latency': latency, 'status': 'timeout'}
        return {'latency': latency, 'status': 'ok'}
    except errors.PingError as e:
        return {'latency': 0, 'status': 'error', 'message': str(e)}
    except Exception as e:
        return {'latency': 0, 'status': 'error', 'message': f'Unexpected error: {str(e)}'}


def track_websites():
    while True:
        results = {target: ping_target(target) for target in WEBSITE_TARGETS}
        with data_lock:
            current_time = time.time() - start_time
            updates = {}
            for target in WEBSITE_TARGETS:
                new_result = PingResult(
                    time=current_time,
                    latency=results[target]['latency'],
                    status=results[target]['status']
                )
                website_data[target].append(new_result)
                if len(website_data[target]) > CONFIG['MAX_HISTORY']:
                    website_data[target].pop(0)
                updates[target] = {
                    'time': new_result.time,
                    'latency': new_result.latency,
                    'status': new_result.status
                }
            socketio.emit('latency_update', updates)
        time.sleep(CONFIG['WEBSITE_PING_INTERVAL'])


def track_drones():
    while True:
        current_targets = DRONE_TARGETS.copy()
        results = {}
        for target, ip in current_targets.items():
            results[target] = ping_target(ip)

        with data_lock:
            current_time = time.time() - start_time
            updates = {}
            for target, ip in current_targets.items():
                if target not in results:
                    continue
                new_result = PingResult(
                    time=current_time,
                    latency=results[target]['latency'],
                    status=results[target]['status']
                )
                if target not in drone_data:
                    drone_data[target] = []
                drone_data[target].append(new_result)
                if len(drone_data[target]) > CONFIG['MAX_HISTORY']:
                    drone_data[target].pop(0)
                updates[target] = {
                    'time': new_result.time,
                    'latency': new_result.latency,
                    'status': new_result.status
                }
            if updates:
                socketio.emit('drone_update', updates)
        time.sleep(CONFIG['DRONE_PING_INTERVAL'])


@app.route('/')
def index():
    return render_template('index.html', targets=WEBSITE_TARGETS, config=CONFIG)


@app.route('/drones')
def drones():
    return render_template('drones.html', targets=DRONE_TARGETS, config=CONFIG)


@app.route('/api/drones', methods=['GET', 'POST', 'DELETE'])
def manage_drones():
    if request.method == 'GET':
        return jsonify(DRONE_TARGETS)

    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'error': 'Invalid data'}), 400

    if request.method == 'POST':
        if 'ip' not in data:
            return jsonify({'error': 'IP address required'}), 400
        DRONE_TARGETS[data['name']] = data['ip']
        with data_lock:
            drone_data[data['name']] = []
        socketio.emit('drone_added', {'name': data['name'], 'ip': data['ip']})
        return jsonify({'success': True})

    elif request.method == 'DELETE':
        if data['name'] in DRONE_TARGETS:
            ip = DRONE_TARGETS[data['name']]
            del DRONE_TARGETS[data['name']]
            with data_lock:
                if data['name'] in drone_data:
                    del drone_data[data['name']]
            socketio.emit('drone_removed', {'name': data['name'], 'ip': ip})
            return jsonify({'success': True})
        return jsonify({'error': 'Drone not found'}), 404

@app.route('/history/websites')
def get_website_history():
    with data_lock:
        return json.dumps({
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in website_data[target]]
            for target in WEBSITE_TARGETS
        })


@app.route('/history/drones')
def get_drone_history():
    with data_lock:
        return json.dumps({
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in drone_data[target]]
            for target in DRONE_TARGETS
        })


@socketio.on('connect')
def handle_connect():
    if not hasattr(app, 'website_thread'):
        app.website_thread = threading.Thread(target=track_websites)
        app.website_thread.daemon = True
        app.website_thread.start()

    if not hasattr(app, 'drone_thread'):
        app.drone_thread = threading.Thread(target=track_drones)
        app.drone_thread.daemon = True
        app.drone_thread.start()

    with data_lock:
        socketio.emit('initial_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in website_data[target]]
            for target in WEBSITE_TARGETS
        })

        socketio.emit('initial_drone_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in drone_data[target]]
            for target in DRONE_TARGETS
        })


@app.teardown_appcontext
def cleanup(exception=None):
    if hasattr(app, 'ping_listener'):
        app.ping_listener.stop()


if __name__ == '__main__':
    app.ping_listener = PingListener(socketio)
    app.ping_listener.daemon = True
    app.ping_listener.start()
    socketio.run(app, debug=True, host='0.0.0.0')