# -*- coding: utf-8 -*-
"""Migra UMA movimentacao 2026 incrementalmente: primeiro apaga, depois migra
em lotes de Ordem (checkpoint a cada 50000). Se interromper, continua."""
import pyodbc, psycopg2, paramiko, threading, socket, time, sys
from decimal import Decimal
from psycopg2.extras import execute_values

SQL_DSN = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"

table = sys.argv[1]
BATCH = 1000

sconn = pyodbc.connect(SQL_DSN, timeout=30)
scur = sconn.cursor()
tunnel = paramiko.SSHClient()
tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
tunnel.connect(VPS_HOST, username=VPS_USER, password=VPS_PWD, timeout=30)
transport = tunnel.get_transport()
local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
local.bind(("127.0.0.1", 15434)); local.listen(5)
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
pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real", user="postgres", password=PG_PWD, connect_timeout=30)
pconn.autocommit = False
pcur = pconn.cursor()
pcur.execute("SET session_replication_role = replica")
pconn.commit()

# colunas
pcur.execute("""SELECT column_name FROM information_schema.columns
    WHERE table_name=%s AND table_schema='public' ORDER BY ordinal_position""", (table,))
cols = [r[0] for r in pcur.fetchall()]
s_cols = [r[0] for r in scur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME=? AND TABLE_SCHEMA='dbo' ORDER BY ORDINAL_POSITION""", table).fetchall()]
s_set = {x.lower(): x for x in s_cols}
cols = [c for c in cols if c.lower() in s_set]
colnames = ', '.join('"%s"' % c for c in cols)

# ja migrado? (checkpoint por max Ordem no PG)
pcur.execute('SELECT coalesce(max("Ordem"), 0) FROM "%s"' % table)
max_ordem = pcur.fetchone()[0]
pcur.execute('SELECT count(*) FROM "%s"' % table)
ja = pcur.fetchone()[0]
print("PG atual: %d rows, max Ordem=%s" % (ja, max_ordem))

# ordens 2026 do SQL (todas)
scur.execute("SELECT Ordem FROM dbo.[%s] WHERE Data_Alteracao >= '2026-01-01' AND Data_Alteracao < '2027-01-01'" % table)
sql_ordens = sorted(r[0] for r in scur.fetchall())
print("SQL ordens 2026:", len(sql_ordens))

# ordens ja no PG
pcur.execute('SELECT "Ordem" FROM "%s"' % table)
pg_ordens = set(r[0] for r in pcur.fetchall())
faltam = [o for o in sql_ordens if o not in pg_ordens]
print("faltam:", len(faltam))

insert = 'INSERT INTO "%s" (%s) OVERRIDING SYSTEM VALUE VALUES %%s' % (table, colnames)
n = 0
t0 = time.time()
for i in range(0, len(faltam), BATCH):
    chunk = faltam[i:i+BATCH]
    q = 'SELECT %s FROM dbo.[%s] WHERE Ordem IN (%s)' % (', '.join(cols), table, ','.join('?'*len(chunk)))
    scur.execute(q, chunk)
    data = []
    for r in scur.fetchall():
        vals = []
        for v in r:
            if v is None: vals.append(None)
            elif type(v) is bool: vals.append(v)
            elif type(v) in (int, float): vals.append(v)
            elif isinstance(v, str): vals.append(v)
            elif isinstance(v, (pyodbc.Timestamp, pyodbc.Date, pyodbc.Time)) or hasattr(v,'year'): vals.append(v)
            elif isinstance(v, Decimal): vals.append(v)
            else: vals.append(str(v))
        data.append(vals)
    execute_values(pcur, insert, data, page_size=BATCH)
    pconn.commit()
    n += len(data)
    if n % 20000 < BATCH:
        print("  ... %d de %d (%.0fs)" % (n, len(faltam), time.time()-t0), flush=True)
print("OK %s: %d rows em %.0fs" % (table, n, time.time()-t0))
pcur.execute("SET session_replication_role = DEFAULT")
pconn.commit()
pconn.close(); sconn.close(); tunnel.close(); local.close()
