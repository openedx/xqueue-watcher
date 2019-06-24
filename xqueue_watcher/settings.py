import json

MANAGER_CONFIG_DEFAULTS = {
    'HTTP_BASIC_AUTH': None,
    'POLL_TIME': 10,
    'REQUESTS_TIMEOUT': 1,
    'POLL_INTERVAL': 1,
    'LOGIN_POLL_INTERVAL': 5,
    'FOLLOW_CLIENT_REDIRECTS': False
}


def get_manager_config_values(app_config_path):
    if not app_config_path.exists():
        return MANAGER_CONFIG_DEFAULTS.copy()
    with open(app_config_path) as config:
        config_tokens = json.load(config)
        return {
            config_key: config_tokens.get(config_key, default_config_value)
            for config_key, default_config_value in MANAGER_CONFIG_DEFAULTS.items()
        }
