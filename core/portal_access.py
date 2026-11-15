"""Owner-account gate for a public single-account server. Stores only a hash."""
import hashlib
import hmac
import json
from pathlib import Path

from core.account_login import AccountError


def account_guard(path):
    record=json.loads(Path(path).read_text(encoding="utf-8"))
    salt=bytes.fromhex(record["salt"])
    def check(request):
        digest=hashlib.scrypt(request.password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32).hex()
        if not (request.login==int(record["login"]) and request.account_type==record["account_type"]
                and request.server==record["server"] and not request.terminal_path
                and hmac.compare_digest(digest,record["digest"])):
            raise AccountError("اطلاعات ورود به این حساب معتبر نیست.")
    return check
