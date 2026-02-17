import hmac
import hashlib
from urllib.parse import parse_qsl


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    parsed_data = dict(parse_qsl(init_data, strict_parsing=True))

    hash_value = parsed_data.pop("hash", None)
    if not hash_value:
        return False

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed_data.items())
    )

    secret_key = hashlib.sha256(bot_token.encode()).digest()

    hmac_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac_hash == hash_value
