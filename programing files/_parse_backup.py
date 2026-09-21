# -*- coding: utf-8 -*-
"""prod backup(.sql.gz)からsubjects/instructors/course_sections/reviewsの行を
安全にパースするユーティリティ（core/backup.pyのdump形式専用パーサー）。
一回限りのdev DB反映作業用。作業完了後は削除してよい。"""
import gzip
import json
import re


def parse_paren_group(s, start_idx):
    assert s[start_idx] == '('
    i = start_idx + 1
    depth = 1
    buf = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'":
            buf.append(c)
            i += 1
            while i < n:
                if s[i] == "'" and i + 1 < n and s[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                buf.append(s[i])
                if s[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        elif c == '(':
            depth += 1
            buf.append(c)
            i += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return ''.join(buf), i + 1
            buf.append(c)
            i += 1
        else:
            buf.append(c)
            i += 1
    raise ValueError("unbalanced parens")


def split_top_level(s):
    vals = []
    i = 0
    n = len(s)
    cur = []
    depth = 0
    while i < n:
        c = s[i]
        if c == "'":
            cur.append(c)
            i += 1
            while i < n:
                if s[i] == "'" and i + 1 < n and s[i + 1] == "'":
                    cur.append("''")
                    i += 2
                    continue
                cur.append(s[i])
                if s[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        elif c == '(':
            depth += 1
            cur.append(c)
            i += 1
        elif c == ')':
            depth -= 1
            cur.append(c)
            i += 1
        elif c == ',' and depth == 0:
            vals.append(''.join(cur).strip())
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    if cur:
        vals.append(''.join(cur).strip())
    return vals


def parse_literal(v):
    v = v.strip()
    if v == 'NULL':
        return None
    if v == 'TRUE':
        return True
    if v == 'FALSE':
        return False
    if v.startswith("'") and v.endswith("'::jsonb"):
        inner = v[1:-len("'::jsonb")]
        inner = inner[:-1] if inner.endswith("'") else inner
        inner = inner.replace("''", "'")
        return json.loads(inner)
    if v.startswith("'") and v.endswith("'"):
        inner = v[1:-1].replace("''", "'")
        return inner
    try:
        if '.' in v or 'e' in v.lower():
            return float(v)
        return int(v)
    except ValueError:
        return v


def load_table_rows(sql_text, table_name):
    pattern = re.compile(r'INSERT INTO "%s" \(' % re.escape(table_name))
    rows = []
    columns = None
    for m in pattern.finditer(sql_text):
        col_start = m.end() - 1
        col_content, after_cols = parse_paren_group(sql_text, col_start)
        cols = [c.strip().strip('"') for c in split_top_level(col_content)]
        if columns is None:
            columns = cols
        values_kw_idx = sql_text.index('(', after_cols)
        val_content, after_vals = parse_paren_group(sql_text, values_kw_idx)
        raw_vals = split_top_level(val_content)
        vals = [parse_literal(v) for v in raw_vals]
        rows.append(dict(zip(cols, vals)))
    return columns, rows


def load_backup(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return f.read()
