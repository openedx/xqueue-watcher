
MANAGER_CONFIG_DEFAULTS = {
    'HTTP_BASIC_AUTH': None,
    'POLL_TIME': 10,
    'REQUESTS_TIMEOUT': 1,
    'POLL_INTERVAL': 1,
    # if this is set, the client will poll at this interval if the queue is empty
    # (meaning, it's polled three times at POLL_INTERVAL without processing anything new)
    'IDLE_POLL_INTERVAL': 0,
    'LOGIN_POLL_INTERVAL': 5,
    'FOLLOW_CLIENT_REDIRECTS': False
}
