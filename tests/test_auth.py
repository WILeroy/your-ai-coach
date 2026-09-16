"""认证模块单元测试"""
import os
from datetime import datetime, timedelta


def test_check_password():
    import auth
    os.environ["ACCESS_PASSWORD"] = "test-pw-123"
    assert auth.check_password("test-pw-123") is True
    assert auth.check_password("wrong") is False
    assert auth.check_password("") is False
    assert auth.check_password(None) is False


def test_default_password_rejected():
    import auth
    os.environ["ACCESS_PASSWORD"] = "change-me-please"
    assert auth.check_password("change-me-please") is False


def test_lockout_flow():
    """5次失败后锁定"""
    from db import get_db, init_db
    import auth
    ip = "1.2.3.4"
    for _ in range(5):
        auth.record_fail(ip)
    locked, retry = auth.is_locked(ip)
    assert locked is True
    assert retry >= 1
    # 清理
    auth.clear_fails(ip)
    assert auth.is_locked(ip)[0] is False
