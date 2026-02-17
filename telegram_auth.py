import hashlib
import hmac
from urllib.parse import parse_qsl
from typing import Dict, Any

def verify_init_data(init_data: str, bot_token: str) -> Dict[str, Any]:
    # Telegram WebApp initData validation:
    # https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    if not init_data:
        raise ValueError("No initData")

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", "")
    if not received_hash:
        raise ValueError("No hash")

    check_arr = []
    for k in sorted(data.keys()):
        check_arr.append(f"{k}={data[k]}")
    check_string = "\n".join(check_arr)

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise ValueError("Bad initData hash")

    # user field is JSON string
    import json
    user = json.loads(data.get("user", "{}"))
    return user
