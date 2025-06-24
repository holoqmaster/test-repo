from ping3 import ping
import zmq
import time
import json
from config import CONFIG, ALL_DRONE_IPS
import threading
from datetime import datetime
import os

class DronePinger:
    def __init__(self, ip_addr_):
        self.ip_address = ip_addr_
        print("self.ip_address = {}".format(self.ip_address))

        self.all_drone_ips = ALL_DRONE_IPS
        for index, drone_ip in enumerate(self.all_drone_ips):
            if drone_ip == self.ip_address:
                self.agent_id = index
        print("My agent id is: ", self.agent_id)

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind("tcp://*:5556")
        print("Drone Pinger started on port 5556")
        self.results = {}
        self.lock = threading.Lock()
        self.ping_count = 0
        
    def ping_all_drones(self):
        threads = []
        for index, drone_ip in enumerate(self.all_drone_ips):
            if drone_ip != self.ip_address:
                print("Pinging drone_ip: {}".format(drone_ip))
                title = "Agent " + str(self.agent_id) + " ping Agent " + str(index)
                t = threading.Thread(target=self.ping_one, args=(title, drone_ip), daemon=True)
                t.start()
                threads.append(t)
                
        for t in threads:
            t.join(CONFIG['DRONE_TIMEOUT'] / 1000 + 0.2)
    
    def ping_one(self, title, ip):
        try:
            # Get raw ping result
            raw_latency = ping(ip, timeout=CONFIG['DRONE_TIMEOUT'] / 1000, unit='ms')

            # Convert and validate
            latency = float(raw_latency) if raw_latency is not None else CONFIG['DRONE_TIMEOUT']
            status = 'timeout' if latency >= CONFIG['DRONE_TIMEOUT'] else 'ok'

            with self.lock:
                self.results[title] = {
                    'latency': latency,
                    'status': status,
                    'ip': ip
                }
            return True
        except Exception as e:
            print(f"❌ Ping error for {title}: {str(e)}")
            with self.lock:
                self.results[title] = {
                    'latency': CONFIG['DRONE_TIMEOUT'],
                    'status': 'error',
                    'ip': ip
                }
            return False

    def format_ping_result(self, target, result):
        status_icons = {
            'ok': '✅',
            'timeout': '⚠️',
            'error': '❌'
        }
        icon = status_icons.get(result['status'], '❓')
        return f"{icon} {target.ljust(10)} ({result['ip']}): {result['latency']:.2f}ms"

    def print_ping_summary(self, results):
        print(f"\n📊 Ping Summary #{self.ping_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        for target, result in results.items():
            print(self.format_ping_result(target, result))

        # Calculate statistics
        successful = sum(1 for r in results.values() if r['status'] == 'ok')
        timeouts = sum(1 for r in results.values() if r['status'] == 'timeout')
        errors = sum(1 for r in results.values() if r['status'] == 'error')

        print("\n📈 Statistics:")
        print(f"✅ Successful: {successful} | ⚠️ Timeouts: {timeouts} | ❌ Errors: {errors}")
        print("=" * 60 + "\n")

    def ping_loop(self):
        while True:
            try:
                self.ping_count += 1
                start_time = time.time()

                self.results = {}
                self.ping_all_drones() # Create threads that update ping results inside self.results 
                self.print_ping_summary(self.results)

                message = {
                    "type": "drone_ping",
                    "timestamp": start_time,
                    "ping_count": self.ping_count,
                    "results": self.results
                }

                # Send the message .....
                self.socket.send_string(f"Ping {json.dumps(message)}")

                # Calculate and sleep for remaining interval time
                elapsed = time.time() - start_time
                sleep_time = max(0, CONFIG['DRONE_PING_INTERVAL'] - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                print(f"\n🔥 Critical error in ping loop: {str(e)}")
                print("🔄 Retrying in 5 seconds...\n")
                time.sleep(5)


if __name__ == "__main__":
    ip_addr = os.getenv('MY_IP_ADDR')
    pinger = DronePinger(ip_addr)
    try:
        pinger.ping_loop()
    except KeyboardInterrupt:
        print("\n🛑 Drone pinger stopped by user")