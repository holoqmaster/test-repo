# Website targets to monitor
WEBSITE_TARGETS = [
    'cloudflare.com', 'google.com', 'amazon.com', 'github.com',
    'microsoft.com', 'twitter.com', 'reddit.com', 'wikipedia.org',
    'stackoverflow.com', 'digitalocean.com', 'linode.com', 'heroku.com'
]

# Drone network IP addresses (can be modified at runtime)
DRONE_TARGETS = {
    'drone 1 to drone 2': '192.168.65.2',
    'drone 1 to drone 3 ': '8.8.8.8'
}

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
    'MAX_HISTORY': 1000,  # data points per target

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