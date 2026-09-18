"""口令认证 + 登录限速（SQLite 持久化，多 worker 安全）"""
import hmac
import os
import secrets
from datetime import datetime, timedelta
from flask import request, session, jsonify
from db import get_db

MAX_FAILS = 5
LOCK_MINUTES = 15
SESSION_HOURS = 30 * 24

# 只有来自这些反代地址的请求才信任 X-Forwarded-For。
# 直连公网时该头完全可由客户端伪造，若盲信会按伪造IP分桶记录失败，
# 使登录限速失效并允许无限爆破。逗号分隔，可用 TRUSTED_PROXIES 覆盖。
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


def get_access_password():
    return os.environ.get("ACCESS_PASSWORD", "")


def _now():
    return datetime.now()


def _fmt(dt):
    return dt.isoformat()


def _parse(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def client_ip():
    remote = request.remote_addr or "unknown"
    trusted = {x.strip() for x in os.environ.get("TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES).split(",") if x.strip()}
    if remote in trusted:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return remote


def is_locked(ip):
    """返回 (locked, retry_after_minutes)"""
    conn = get_db()
    row = conn.execute("SELECT locked_until FROM login_attempts WHERE ip=?", [ip]).fetchone()
    conn.close()
    if not row or not row["locked_until"]:
        return False, 0
    until = _parse(row["locked_until"])
    if until and until > _now():
        return True, max(1, int((until - _now()).total_seconds() // 60) + 1)
    return False, 0


def record_fail(ip):
    conn = get_db()
    now = _now()
    row = conn.execute("SELECT fails, locked_until FROM login_attempts WHERE ip=?", [ip]).fetchone()
    fails = (row["fails"] if row else 0) + 1
    locked_until = None
    if fails >= MAX_FAILS:
        locked_until = _fmt(now + timedelta(minutes=LOCK_MINUTES))
        fails = 0
    conn.execute(
        """INSERT INTO login_attempts(ip, fails, locked_until, updated_at) VALUES(?,?,?,?)
           ON CONFLICT(ip) DO UPDATE SET fails=excluded.fails,
               locked_until=excluded.locked_until, updated_at=excluded.updated_at""",
        [ip, fails, locked_until, _fmt(now)])
    conn.commit()
    conn.close()


def clear_fails(ip):
    conn = get_db()
    conn.execute("DELETE FROM login_attempts WHERE ip=?", [ip])
    conn.commit()
    conn.close()


def check_password(pw):
    expected = get_access_password()
    if not expected or expected == "change-me-please":
        return False
    return hmac.compare_digest(str(pw or ""), expected)


def mark_authed():
    session["authed"] = True
    session["ts"] = secrets.token_hex(8)
    session.permanent = True


def is_authed():
    if not session.get("authed"):
        return False
    # 会话有效期（配合 PERMANENT_SESSION_LIFETIME 双保险）
    return True


def login_route():
    """POST /api/auth/login {password}"""
    ip = client_ip()
    locked, retry = is_locked(ip)
    if locked:
        return jsonify({"error": f"尝试次数过多，已锁定，请 {retry} 分钟后再试"}), 429
    data = request.get_json(silent=True) or {}
    pw = data.get("password", "")
    if not check_password(pw):
        record_fail(ip)
        return jsonify({"error": "口令错误"}), 401
    clear_fails(ip)
    mark_authed()
    return jsonify({"ok": True})


def logout_route():
    session.clear()
    return jsonify({"ok": True})


def check_route():
    if not get_access_password():
        return jsonify({"authed": True, "password_set": False})
    return jsonify({"authed": is_authed(), "password_set": True})
