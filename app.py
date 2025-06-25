from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import time
import threading
from ping3 import ping, errors
from config import ALL_DRONE_IPS, ALL_DRONE_TARGETS, CONFIG
from dataclasses import dataclass
import json
import zmq
from threading import Thread
import os

# This script monitors the latency of drones and websites, providing real-time updates via a web interface.
# It uses Flask for the web server and Flask-SocketIO for real-time communication.

drone_number = "drone_1"
DRONE_TARGETS = ALL_DRONE_TARGETS[drone_number]
my_ip_addr = os.getenv('MY_IP_ADDR')

# Initialize Data storage
gcs_to_drone_latency_data = {
    target: [] for target in ALL_DRONE_IPS 
}# ground control station (the one running this script) to drone latency data
drone_to_drone_latency_data = {target: [] for target in DRONE_TARGETS} # latency in between drones
data_lock = threading.Lock()
start_time = time.time()

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

@dataclass
class PingResult:
    time: float
    latency: float
    status: str

class InterDroneLatencyTracker(Thread):
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
                    drone_to_drone_latency_data.setdefault(target, [])
                    drone_to_drone_latency_data[target].append(PingResult(
                        time=current_time,
                        latency=latency,
                        status=status
                    ))
                    # Keep only the most recent data
                    if len(drone_to_drone_latency_data[target]) > CONFIG['MAX_HISTORY']:
                        drone_to_drone_latency_data[target].pop(0)

            except (ValueError, KeyError) as e:
                print(f"Error processing {target} data: {e}")

        if updates:
            self.socketio.emit('drone_update', updates)

    def run(self):
        while self.running:
            try:
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
            except zmq.error.ContextTerminated:
                print("ZeroMQ context terminated, exiting thread.")
                break
            except Exception as e:
                print(f"Error during polling: {e}")
                break

    def stop(self):
        print("Closing InterDroneLatencyTracker Thread") 
        self.running = False
        self.join() # Wait for thread to finish
        print("Closing ZMQ sockets") 
        self.socket.close()
        print("Terminating ZeroMQ context") 
        self.context.term()

class DroneLatencyTracker(Thread):
    def __init__(self, socketio_):
        super().__init__()
        self.socketio = socketio_
        self.gcs_to_drone_latency_result = {}
        self.gcs_to_drone_lock = threading.Lock()
        
    def ping_one(self,ip):
        try:
            # Get raw ping result
            raw_latency = ping(ip, timeout=CONFIG['DRONE_TIMEOUT'] / 1000, unit='ms')

            # Convert and validate
            latency = float(raw_latency) if raw_latency is not None else CONFIG['DRONE_TIMEOUT']
            status = 'timeout' if latency >= CONFIG['DRONE_TIMEOUT'] else 'ok'

            with self.gcs_to_drone_lock:
                self.gcs_to_drone_latency_result[ip] = {
                    'latency': latency,
                    'status': status,
                    'ip': ip
                }
            return True
        except Exception as e:
            print(f"❌ Ping error for {ip}: {str(e)}")
            with self.gcs_to_drone_lock:
                self.gcs_to_drone_latency_result[ip] = {
                    'latency': CONFIG['DRONE_TIMEOUT'],
                    'status': 'error',
                    'ip': ip
                }
            return False
            
    def run(self):
        while True:
            threads = []
            for index, drone_ip in enumerate(ALL_DRONE_IPS):
                t = threading.Thread(target=self.ping_one, args=(drone_ip,), daemon=True)
                t.start()
                threads.append(t)
                    
            for t in threads:
                t.join(CONFIG['DRONE_TIMEOUT'] / 1000 + 0.2)

            with data_lock:
                current_time = time.time() - start_time
                updates = {}
                for target, result in self.gcs_to_drone_latency_result.items():
                    new_result = PingResult(
                        time=current_time,
                        latency=result['latency'],
                        status=result['status']
                    )
                    gcs_to_drone_latency_data[target].append(new_result)
                    if len(gcs_to_drone_latency_data[target]) > CONFIG['MAX_HISTORY']:
                        gcs_to_drone_latency_data[target].pop(0)
                    updates[target] = {
                        'time': new_result.time,
                        'latency': new_result.latency,
                        'status': new_result.status
                    }
                self.socketio.emit('latency_update', updates)

    def stop(self):
        print("Closing DroneLatencyTracker Thread..") 
        self.running = False

@app.route('/')
def index():
    return render_template('index.html', targets=ALL_DRONE_IPS, config=CONFIG)

@app.route('/drones')
def drones():
    return render_template('drones.html', targets=DRONE_TARGETS, config=CONFIG)

@socketio.on('connect')
def handle_connect():

    # Emit initial data to the clients    
    with data_lock:
        socketio.emit('initial_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in gcs_to_drone_latency_data[target]]
            for target in ALL_DRONE_IPS
        })

        socketio.emit('initial_drone_data', {
            target: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in drone_to_drone_latency_data[target]]
            for target in DRONE_TARGETS
        })

@app.teardown_appcontext
def cleanup(exception=None):
    print("Cleanup")

if __name__ == '__main__':
    # 3 processes, inter drone latency tracker, drone pinger and flask app
    inter_drone_listener = InterDroneLatencyTracker(socketio)
    inter_drone_listener.daemon = True
    inter_drone_listener.start()

    drone_pinger = DroneLatencyTracker(socketio)
    drone_pinger.daemon = True
    drone_pinger.start()

    try:
        socketio.run(app, debug=False, host='0.0.0.0')
    finally:
        print("Stopping InterDroneLatencyTracker")
        inter_drone_listener.stop()
        drone_pinger.stop()
        pass 
