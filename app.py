from flask import Flask, render_template
from flask_socketio import SocketIO
import time
import threading
from ping3 import ping
from config import ALL_DRONE_IPS, CONFIG
from dataclasses import dataclass
import json
import zmq
from threading import Thread

# This script monitors the latency of drones and websites, providing real-time updates via a web interface.
# It uses Flask for the web server and Flask-SocketIO for real-time communication.

# Initialize Data storages
GCS_TO_NODES_LATENCY_DATA = {
    ip: [] for ip in ALL_DRONE_IPS 
}# ground control station (the one running this script) to drone latency data
DATA_LOCK = threading.Lock()
START_TIME = time.time()

# INTER_NODE_LATENCY_DATA = {
#     "Agent 0 ping Agent 3": [],
#     "Agent 1 ping Agent 2": [],
#     # ...other chart titles...
# }
INTER_DRONE_KEYS = []
INTER_NODE_LATENCY_DATA = {}
for agent_id in range(len(ALL_DRONE_IPS)):
    for index, drone_ip in enumerate(ALL_DRONE_IPS):
        if index != agent_id:
            title = "Agent " + str(agent_id) + " ping Agent " + str(index)
            INTER_DRONE_KEYS.append(title)
            INTER_NODE_LATENCY_DATA[title]= []

APP = Flask(__name__)
SOCKETIO = SocketIO(APP, async_mode='threading', cors_allowed_origins="*")

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
        self.poller = zmq.Poller()

        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://localhost:5556")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "Ping")

        self.sockets = []
        for ip in ALL_DRONE_IPS:
            socket = self.context.socket(zmq.SUB)
            socket.connect(f"tcp://{ip}:5556")
            print(f"Connecting to:tcp://{ip}:5556")
            socket.setsockopt_string(zmq.SUBSCRIBE, "Ping")
            self.poller.register(socket, zmq.POLLIN)
            self.sockets.append(socket)

    def process_drone_ping(self, data):
        current_time = time.time() - START_TIME
        updates = {}

        # data = 
        # {'type': 'drone_ping', 'timestamp': 1750916399.29212, 'ping_count': 507, 
        # 'results': {'Agent 0 ping Agent 3': {'latency': 4.485607147216797, 'status': 'ok', 'ip': 'google.com'},
        #  'Agent 0 ping Agent 2': {'latency': 1500, 'status': 'timeout', 'ip': '192.168.65.201'}, 
        # 'Agent 0 ping Agent 1': {'latency': 1500, 'status': 'timeout', 'ip': '192.168.65.200'}}} 
        for target, result in data['results'].items():
            try:
                # print(f"Processing ping result for... {target}: {result}")
                latency = float(result['latency'])
                status = result.get('status', 'ok')
                status = 'ok' if status == 'ok' and latency < CONFIG['DRONE_TIMEOUT'] else 'timeout'

                updates[target] = {
                    'time': current_time,
                    'latency': latency,
                    'status': status
                }

                with DATA_LOCK:
                    if target not in INTER_NODE_LATENCY_DATA:
                        print("ERROR: TARGET DOES NOT EXIST.................:", target)

                    INTER_NODE_LATENCY_DATA[target].append(PingResult(
                        time=current_time,
                        latency=latency,
                        status=status
                    ))
                    # Keep only the most recent data
                    if len(INTER_NODE_LATENCY_DATA[target]) > CONFIG['MAX_HISTORY']:
                        INTER_NODE_LATENCY_DATA[target].pop(0)

            except (ValueError, KeyError) as e:
                print(f"Error processing {target} data: {e}")

        if updates:
            # print(f"Emitting updates: {updates}")
            self.socketio.emit('drone_update', updates)

    def run(self):
        while self.running:
            try:
                socks = dict(self.poller.poll(timeout=100))
                for socket in self.sockets:
                    if socket in socks:
                        try:
                            message = socket.recv_string()
                            if message.startswith("Ping "):
                                data = json.loads(message[5:])
                                if data.get('type') == 'drone_ping':
                                    # print("Processing data: {}".format(data))
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

            with DATA_LOCK:
                current_time = time.time() - START_TIME
                updates = {}
                for target, result in self.gcs_to_drone_latency_result.items():
                    new_result = PingResult(
                        time=current_time,
                        latency=result['latency'],
                        status=result['status']
                    )
                    GCS_TO_NODES_LATENCY_DATA[target].append(new_result)
                    if len(GCS_TO_NODES_LATENCY_DATA[target]) > CONFIG['MAX_HISTORY']:
                        GCS_TO_NODES_LATENCY_DATA[target].pop(0)
                    updates[target] = {
                        'time': new_result.time,
                        'latency': new_result.latency,
                        'status': new_result.status
                    }
                self.socketio.emit('latency_update', updates)

    def stop(self):
        print("Closing DroneLatencyTracker Thread..") 
        self.running = False

@APP.route('/')
def index():
    return render_template('index.html', data=ALL_DRONE_IPS, config=CONFIG)

@APP.route('/drones')
def drones():
    return render_template('drones.html', data=INTER_NODE_LATENCY_DATA, config=CONFIG, nodes_ip=ALL_DRONE_IPS)

@SOCKETIO.on('connect')
def handle_connect():
    # Emit latency data to the connected clients
    with DATA_LOCK:
        SOCKETIO.emit('latency_data', {
            ip: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in GCS_TO_NODES_LATENCY_DATA[ip]]
            for ip in ALL_DRONE_IPS
        })

        SOCKETIO.emit('inter_drone_latency_data', {
            keys: [{
                'time': r.time,
                'latency': r.latency,
                'status': r.status
            } for r in INTER_NODE_LATENCY_DATA[keys]]
            for keys in INTER_DRONE_KEYS
        })

@APP.teardown_appcontext
def cleanup(exception=None):
    print("Cleanup")

if __name__ == '__main__':
    # 3 processes, inter drone latency tracker, drone pinger and flask APP
    inter_drone_listener = InterDroneLatencyTracker(SOCKETIO)
    inter_drone_listener.daemon = True
    inter_drone_listener.start()

    drone_pinger = DroneLatencyTracker(SOCKETIO)
    drone_pinger.daemon = True
    drone_pinger.start()

    try:
        SOCKETIO.run(APP, debug=False, host='0.0.0.0')
    finally:
        print("Stopping InterDroneLatencyTracker")
        inter_drone_listener.stop()
        drone_pinger.stop()
