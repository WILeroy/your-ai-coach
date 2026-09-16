import os, sys, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import db as db_mod


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """每个测试使用独立的临时数据库副本，避免污染真实数据"""
    src = db_mod.DB_PATH
    dst = tmp_path / "test.db"
    if os.path.exists(src):
        shutil.copy2(src, dst)
    monkeypatch.setattr(db_mod, "DB_PATH", str(dst))
    db_mod.init_db()
    yield str(dst)
