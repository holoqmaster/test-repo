# The things are hard coded for now...

# Ping targets to monitor
ALL_DRONE_IPS = [
    '192.168.65.199', '192.168.65.200', '192.168.65.201', 'google.com'
]

# Configuration parameters
CONFIG = {
    # Website monitoring
    'WEBSITE_PING_TIMEOUT': 1.5,  # seconds
    'WEBSITE_PING_INTERVAL': 1,  # seconds

    # Drone monitoring
    'DRONE_PING_INTERVAL': 1,  # seconds
    'DRONE_TIMEOUT': 1500,  # ms

    # Display settings
    'TIMEOUT_THRESHOLD': 2000,  # ms (max chart value)
    'LATENCY_TIMEOUT': 1500,  # ms (considered timeout)
    'MAX_HISTORY': 100,  # data points per target

    # Status colors
    'STATUS_COLORS': {
        'ok': '#2ecc71',  # green
        'timeout': '#f39c12',  # orange
        'error': '#e74c3c'  # red
    },

    # Drone status thresholds (ms)
    'DRONE_THRESHOLDS': {
        'good': 500,  # green
        'warning': 1000,  # yellow
        'critical': 1500  # red
    }
}