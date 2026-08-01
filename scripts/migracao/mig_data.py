# -*- coding: utf-8 -*-
"""Migra dados do SQL Server (S9_Real) -> PostgreSQL (s9_real na VPS).
- Cadastro: copia tudo
- Movimentacao (com Data_Alteracao): copia so 2026
- Pula: Log_*, Contagem_*, e movs sem Data_Alteracao
"""
import pyodbc, psycopg2, paramiko, threading, socket, json, sys, time
from decimal import Decimal
from psycopg2.extras import execute_values

VPS_HOST = "84.247.189.155"
VPS_USER = "root"
VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"
SQL_DSN = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
BATCH = 1000

# ---------- tunnel ----------
def open_tunnel():
    tunnel = paramiko.SSHClient()
    tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    tunnel.connect(VPS_HOST, username=VPS_USER, password=VPS_PWD, timeout=30)
    transport = tunnel.get_transport()
    local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local.bind(("127.0.0.1", 15434))
    local.listen(5)
    def fwd():
        while True:
            try:
                client, _ = local.accept()
            except Exception:
                break
            ch = transport.open_channel("direct-tcpip", ("127.0.0.1", 5434), client.getpeername())
            def pipe(src, dst):
                try:
                    while True:
                        d = src.recv(65536)
                        if not d: break
                        dst.sendall(d)
                except Exception: pass
                try: dst.shutdown(2)
                except Exception: pass
            threading.Thread(target=pipe, args=(client, ch), daemon=True).start()
            threading.Thread(target=pipe, args=(ch, client), daemon=True).start()
    threading.Thread(target=fwd, daemon=True).start()
    return tunnel, local

def py_to_sql(val):
    """Convert python value from pyodbc for psycopg2."""
    if val is None:
        return None
    t = type(val)
    if t is bool:
        return val
    if t in (int, float):
        return val
    if t is bytes:
        return psycopg2.Binary(val)
    if isinstance(val, str):
        return val
    if isinstance(val, (pyodbc.Timestamp, pyodbc.Date, pyodbc.Time)) or hasattr(val, 'year'):
        return val
    if isinstance(val, Decimal):
        return val
    return str(val)

def migrate_table(scur, pcur, pconn, table, cols, where=""):
    colnames = ', '.join('"%s"' % c for c in cols)
    q = 'SELECT %s FROM dbo.[%s]' % (', '.join(cols), table)
    if where:
        q += ' WHERE %s' % where
    scur.execute(q)
    insert = 'INSERT INTO "%s" (%s) OVERRIDING SYSTEM VALUE VALUES %%s' % (table, colnames)
    n = 0
    while True:
        rows = scur.fetchmany(BATCH)
        if not rows:
            break
        data = [[py_to_sql(v) for v in r] for r in rows]
        execute_values(pcur, insert, data, page_size=BATCH)
        pconn.commit()
        n += len(rows)
    return n

def main():
    cat = json.load(open(r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\mig_cat.json'))
    rows_meta = json.load(open(r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\sql_rowcounts.json'))
    plan = json.load(open(r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\mig_plan_mov.json'))

    sconn = pyodbc.connect(SQL_DSN, timeout=30)
    scur = sconn.cursor()
    tunnel, local = open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real", user="postgres", password=PG_PWD, connect_timeout=30)
    pconn.autocommit = False
    pcur = pconn.cursor()
    # disable FK checks during bulk load (restored at end)
    pcur.execute("SET session_replication_role = replica")
    pconn.commit()

    # tables to migrate
    todo = []
    for t, c in cat.items():
        if c == 'skip':
            continue
        if rows_meta.get(t, 0) <= 0:
            continue
        if c == 'mov':
            info = plan.get(t, {})
            if not info.get('has_da'):
                continue  # pulado
            todo.append((t, 'mov'))
        else:
            todo.append((t, 'cad'))

    print("tabelas a migrar:", len(todo))
    total_copied = 0
    for t, kind in todo:
        # get PG cols
        pcur.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_name=%s AND table_schema='public' ORDER BY ordinal_position""", (t,))
        pg_cols = [r[0] for r in pcur.fetchall()]
        s_cols = [r[0] for r in scur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND TABLE_SCHEMA='dbo' ORDER BY ORDINAL_POSITION""", t).fetchall()]
        s_set = {x.lower(): x for x in s_cols}
        cols = [c for c in pg_cols if c.lower() in s_set]
        if not cols:
            print("  SKIP %s (sem colunas)" % t)
            continue
        # clear existing data in PG before inserting
        pcur.execute('TRUNCATE TABLE "%s" CASCADE' % t)
        pconn.commit()
        where = ""
        if kind == 'mov':
            where = "Data_Alteracao >= '2026-01-01' AND Data_Alteracao < '2027-01-01'"
        t0 = time.time()
        try:
            n = migrate_table(scur, pcur, pconn, t, cols, where)
            total_copied += n
            print("  %-50s %10d rows (%5.1fs)" % (t, n, time.time() - t0))
        except Exception as e:
            pconn.rollback()
            print("  ERROR %s: %s" % (t, str(e)[:150]))

    print("\nTOTAL copiado:", total_copied)
    # restore FK checks
    pcur.execute("SET session_replication_role = DEFAULT")
    pconn.commit()
    pconn.close()
    sconn.close()
    tunnel.close()
    local.close()

if __name__ == '__main__':
    main()
