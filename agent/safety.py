"""SQL 安全校验: 只允许只读 SELECT 查询"""
import re

FORBIDDEN_KEYWORDS = [
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
    'TRUNCATE', 'VACUUM', 'PRAGMA', 'ATTACH', 'DETACH',
    'REINDEX', 'REPLACE', 'GRANT', 'REVOKE', 'BEGIN',
    'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE',
]

FORBIDDEN_PATTERNS = [
    r';\s*\w',  # multi-statement separator followed by any keyword
    r'/\*.*?\*/',  # block comments (potential hiding of malicious code)
]

def validate_select_sql(sql):
    """验证 SQL 是否为安全的只读 SELECT 语句。
    返回 (is_valid, error_message)
    """
    if not sql or not isinstance(sql, str):
        return False, "SQL 为空"

    stripped = sql.strip()
    if not stripped:
        return False, "SQL 为空"

    clean = _remove_string_literals(stripped)
    first_word = clean.split()[0].upper() if clean.split() else ''

    if first_word not in ('SELECT', 'WITH', 'EXPLAIN'):
        return False, f"禁止的 SQL 操作: {first_word}，仅允许 SELECT/WITH/EXPLAIN 查询"

    upper = stripped.upper()
    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + kw + r'\b'
        if re.search(pattern, upper):
            return False, f"SQL 中包含禁止的关键词: {kw}"

    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, stripped, re.DOTALL):
            return False, "SQL 中包含禁止的模式"

    max_len = 4000
    if len(stripped) > max_len:
        return False, f"SQL 过长 ({len(stripped)}>{max_len}字符)"

    return True, ""

def _remove_string_literals(sql):
    """移除 SQL 中的字符串字面量，避免字符串内容干扰关键词检测"""
    result = []
    i = 0
    while i < len(sql):
        if sql[i] == "'":
            j = i + 1
            while j < len(sql):
                if sql[j] == "'":
                    if j + 1 < len(sql) and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            result.append('?')
            i = j + 1 if j < len(sql) else j
        elif sql[i] == '"':
            j = i + 1
            while j < len(sql):
                if sql[j] == '"':
                    if j + 1 < len(sql) and sql[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            result.append('?')
            i = j + 1 if j < len(sql) else j
        else:
            result.append(sql[i])
            i += 1
    return ''.join(result)

def add_limit(sql, default_limit=50, max_limit=200):
    """如果 SQL 没有 LIMIT 子句，追加默认 LIMIT"""
    stripped = sql.strip().rstrip(';').strip()
    upper = stripped.upper()
    if 'LIMIT' not in upper:
        return f"{stripped} LIMIT {default_limit}"
    return stripped
