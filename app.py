from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import time
import threading
from ping3 import ping, errors
import matplotlib
from config import HOST_TARGETS, ALL_DRONE_TARGETS, CONFIG
from dataclasses import dataclass
import json
import zmq
from threading import Thread

# Temp hardcoded, replace w environment variables when things are more fully set up
drone_number = "drone_1"
DRONE_TARGETS = ALL_DRONE_TARGETS[drone_number]

matplotlib.use('Agg')

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Data storage
website_data = {target: [] for target in HOST_TARGETS}
drone_data = {target: [] for target in DRONE_TARGETS}
data_lock = threading.Lock()
start_time = time.time()

@dataclass
class PingResult:
    time: float
    latency: float
    status: str

class PingListener(Thread):
    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio
        self.running = True
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://localhost:5556")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "Ping")
        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

    def run(self):
        while self.running:
            socks = dict(self.poller.poll(timeout=100))
            if self.socket in socks:
                try:
                    message = self.socket.recv_string()
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
                status = 'ok' if status == 'ok' and latency < CONFIG['DRONE_TIMEOUT'] else 'timeout'

                updates[target] = {
                    'time': current_time,
                    'latency': latency,
                    'status': status
                }

                with data_lock:
                    drone_data.setdefault(target, [])
                    drone_data[target].append(PingResult(
                        time=current_time,
                        latency=latency,
                        status=status
                    ))
                    # Keep only the most recent data
                    if len(drone_data[target]) > CONFIG['MAX_HISTORY']:
                        drone_data[target].pop(0)

            except (ValueError, KeyError) as e:
                print(f"Error processing {target} data: {e}")

        if updates:
            self.socketio.emit('drone_update', updates)

    def stop(self):
        self.running = False
        self.socket.close()
        self.context.term()

def ping_target(target):
    try:
        latency = ping(target, timeout=CONFIG['WEBSITE_PING_TIMEOUT'], unit='ms')
        if latency is None:
            return {'latency': CONFIG['TIMEOUT_THRESHOLD'], 'status': 'timeout'}
        if latency > CONFIG['LATENCY_TIMEOUT']:
            return {'latency': latency, 'status': 'timeout'}
        return {'latency': latency, 'status': 'ok'}
    except errors.PingError:
        return {'latency': 0, 'status': 'error'}
    except Exception:
        return {'latency': 0, 'status': 'error'}

def track_websites():
    while True:
        results = {target: ping_target(target) for target in HOST_TARGETS}
        with data_lock:
            current_time = time.time() - start_time
            updates = {}
            for target, result in results.items():
                new_result = PingResult(
                    time=current_time,
                    latency=result['latency'],
                    status=result['status']
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
    return render_template('index.html', targets=HOST_TARGETS, config=CONFIG)

@app.route('/drones')
def drones():
    return render_template('drones.html', targets=DRONE_TARGETS, config=CONFIG)


@app.route('/history/websites')
def get_website_history():
    with data_lock:
        return jsonify({
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in website_data[target]]
            for target in HOST_TARGETS
        })

@app.route('/history/drones')
def get_drone_history():
    with data_lock:
        return jsonify({
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

    with data_lock:
        socketio.emit('initial_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in website_data[target]]
            for target in HOST_TARGETS
        })

        socketio.emit('initial_drone_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in drone_data[target]]
            for target in DRONE_TARGETS
        })

@app.teardown_appcontext   #cause used daemon threads which automatically exits when main exits
                            # Registers a function to be called when the application context ends
def cleanup(exception=None):
    print("cleanup")
    if hasattr(app, 'ping_listener'):
        print("ping listener stop")
        app.ping_listener.stop()

if __name__ == '__main__':
    ping_listener = PingListener(socketio)
    ping_listener.daemon = True
    ping_listener.start()
    try:
        socketio.run(app, debug=False, host='0.0.0.0')
    finally:
        ping_listener.stop()