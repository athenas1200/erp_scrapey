# -*- coding: utf-8 -*-
import pyodbc, psycopg2, paramiko, threading, socket, json

# --- SQL Server ---
conn_str = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
sconn = pyodbc.connect(conn_str, timeout=30)
scur = sconn.cursor()

# --- PG tunnel ---
tunnel = paramiko.SSHClient()
tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
tunnel.connect("84.247.189.155", username="root", password="Lin1106***", timeout=30)
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

pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real", user="postgres", password="S9pg2026!", connect_timeout=30)
pcur = pconn.cursor()

# SQL tables with data
sql_tables = [r[0] for r in scur.execute("""
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE='BASE TABLE' AND TABLE_SCHEMA='dbo'""").fetchall()]

# PG tables
pcur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
pg_tables = set(r[0] for r in pcur.fetchall())

# SQL tables with Data_Alteracao column
da_tables = set()
for t in sql_tables:
    try:
        r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
        if r[0] > 0:
            da_tables.add(t)
    except Exception:
        pass

missing_in_pg = [t for t in sql_tables if t not in pg_tables]
print("SQL tables:", len(sql_tables))
print("PG tables:", len(pg_tables))
print("SQL tables missing in PG:", len(missing_in_pg))
print("Tables with Data_Alteracao (SQL):", len(da_tables))
json.dump({"sql": sql_tables, "missing_pg": missing_in_pg, "da": sorted(da_tables)},
          open(r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\mig_tables.json', 'w'))
pconn.close()
sconn.close()
tunnel.close()
local.close()
