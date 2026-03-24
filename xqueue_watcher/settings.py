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


def get_xqueue_servers(servers_config_path):
    """
    Load named XQueue server definitions from xqueue_servers.json.

    Returns a dict mapping server names to their connection config dicts,
    each containing 'SERVER' (URL string) and 'AUTH' ([username, password]).
    Returns an empty dict if the file does not exist.

    Raises ValueError if any server entry is missing required keys.
    """
    if not servers_config_path.exists():
        return {}
    with open(servers_config_path) as config:
        servers = json.load(config)
    for name, server_config in servers.items():
        missing = [k for k in ('SERVER', 'AUTH') if k not in server_config]
        if missing:
            raise ValueError(
                f"xqueue_servers.json: server '{name}' is missing required key(s): {missing}"
            )
    return servers
