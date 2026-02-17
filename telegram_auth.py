import hashlib
import hmac
import urllib.parse
from typing import Tuple, Dict, Any


def verify_telegram_init_data(init_data: str, bot_token: str) -> Tuple[bool, Dict[str, Any] | str]:
    try:
        init_data = (init_data or "").strip()
        if not init_data:
            return False, "empty init_data"

        parsed = urllib.parse.parse_qs(init_data, strict_parsing=True)
        data = {k: v[0] for k, v in parsed.items() if v}

        if "hash" not in data:
            return False, "missing hash"

        received_hash = data.pop("hash")

        pairs = [f"{k}={data[k]}" for k in sorted(data.keys())]
        data_check_string = "\n".join(pairs)

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc_hash, received_hash):
            return False, "hash mismatch"

        import json
        user_raw = data.get("user")
        if not user_raw:
            return False, "missing user"

        user = json.loads(user_raw)
        payload: Dict[str, Any] = {"user": user}
        for k in ("query_id", "auth_date", "chat_type", "chat_instance", "start_param"):
            if k in data:
                payload[k] = data[k]

        return True, payload
    except Exception as e:
        return False, f"exception: {e}"
