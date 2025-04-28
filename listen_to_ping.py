import zmq
import time
import json

def listen_to_pings():
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect("tcp://localhost:5556")
    socket.setsockopt_string(zmq.SUBSCRIBE, "Ping")

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while True:
        socks = dict(poller.poll(timeout=100))
        if socket in socks:
            try:
                message = socket.recv_string()
                if message.startswith("Ping "):
                    data = json.loads(message[5:])
                    print("\nReceived ping data:")
                    print(f"Type: {data.get('type')}")
                    print(f"Timestamp: {data.get('timestamp')}")
                    print("Results:")
                    for target, result in data.get('results', {}).items():
                        print(f"  {target}: {result.get('latency')}ms ({result.get('status')})")
            except Exception as e:
                print(f"Error processing message: {e}")
        else:
            time.sleep(0.01)

if __name__ == "__main__":
    listen_to_pings()