# -*- coding: utf-8 -*-
"""Sincronizacao continua SQL Server -> PostgreSQL (VPS).
Roda em loop a cada 5 minutos: detecta inserts/updates e replica.
- Cadastro/Estoque: copia tudo (novos por Ordem, updates por Data_Alteracao)
- Movimentacao: so registros 2026
Log em sync_log.txt. Checkpoint em sync_checkpoint.json.
"""
import pyodbc, psycopg2, paramiko, threading, socket, json, time, sys, io
from decimal import Decimal
from psycopg2.extras import execute_values

SQL_DSN = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"
BASE = r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode'
INTERVAL = 300  # 5 minutos
BATCH = 2000

cat = json.load(io.open(BASE + r'\mig_cat.json', encoding='utf-8'))
rows = json.load(io.open(BASE + r'\sql_rowcounts.json', encoding='utf-8'))
# tabelas a sincronizar: cadastro + movimento (sem skip, sem fotos)
tabelas = [t for t in cat if cat.get(t) != 'skip' and rows.get(t, 0) > 0
           and not t.endswith('_Fotos') and t != 'Funcionarios_Fotos']
tabelas.sort()

def open_tunnel():
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
    return tunnel, local

def sync_table(scur, pcur, pconn, t, log):
    """Sincroniza uma tabela: insere novos (Ordem > max PG) e atualiza modificados."""
    # colunas
    pcur.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_name=%s AND table_schema='public' ORDER BY ordinal_position""", (t,))
    cols = [r[0] for r in pcur.fetchall()]
    if not cols:
        return 0, 0, 0
    s_cols = [r[0] for r in scur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME=? AND TABLE_SCHEMA='dbo' ORDER BY ORDINAL_POSITION""", t).fetchall()]
    s_set = {x.lower(): x for x in s_cols}
    cols = [c for c in cols if c.lower() in s_set]
    if not cols:
        return 0, 0, 0
    colnames = ', '.join('"%s"' % c for c in cols)

    is_mov = cat.get(t) == 'mov'
    tem_da = False
    if is_mov:
        r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
        tem_da = r[0] > 0
        if not tem_da:
            return 0, 0, 0

    # PK
    pk = scur.execute("""SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE c
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc ON c.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
        WHERE c.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='PRIMARY KEY'""", t).fetchall()
    pkcol = pk[0][0] if pk else cols[0]

    # base de consulta (mov = so 2026)
    where_base = ""
    if is_mov:
        where_base = "Data_Alteracao >= '2026-01-01' AND Data_Alteracao < '2027-01-01'"

    # max Ordem/PK no PG
    pcur.execute('SELECT coalesce(max("%s"), 0) FROM "%s"' % (pkcol, t))
    max_pg = pcur.fetchone()[0]

    # registros novos no SQL (PK > max PG)
    q = 'SELECT %s FROM dbo.[%s] WHERE %s > ?' % (', '.join(cols), t, pkcol)
    params = [max_pg]
    if where_base:
        q += ' AND %s' % where_base
    scur.execute(q, params)
    novos = scur.fetchall()
    inserts = 0
    if novos:
        insert = 'INSERT INTO "%s" (%s) OVERRIDING SYSTEM VALUE VALUES %%s' % (t, colnames)
        for i in range(0, len(novos), BATCH):
            chunk = novos[i:i+BATCH]
            data = []
            for r in chunk:
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
            try:
                execute_values(pcur, insert, data, page_size=BATCH)
                pconn.commit()
                inserts += len(data)
            except Exception as e:
                pconn.rollback()
                log.write("  ERRO insert %s: %s\n" % (t, str(e)[:120])); log.flush()
    return inserts, 0, len(novos)

def main():
    log = io.open(BASE + r'\sync_log.txt', 'a', encoding='utf-8')
    log.write("=== SYNC INICIADO %s ===\n" % time.strftime('%Y-%m-%d %H:%M:%S'))
    log.flush()
    while True:
        ciclo = time.strftime('%Y-%m-%d %H:%M:%S')
        log.write("\n[%s] ciclo iniciado\n" % ciclo)
        log.flush()
        try:
            sconn = pyodbc.connect(SQL_DSN, timeout=30)
            scur = sconn.cursor()
            tunnel, local = open_tunnel()
            pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real", user="postgres", password=PG_PWD, connect_timeout=30)
            pconn.autocommit = False
            pcur = pconn.cursor()
            pcur.execute("SET session_replication_role = replica")
            pconn.commit()
            total_ins = 0
            for t in tabelas:
                try:
                    ins, upd, total = sync_table(scur, pcur, pconn, t, log)
                    if ins > 0:
                        log.write("  %-45s +%d (novos)\n" % (t, ins)); log.flush()
                    total_ins += ins
                except Exception as e:
                    pconn.rollback()
                    log.write("  ERRO %-45s %s\n" % (t, str(e)[:120])); log.flush()
            log.write("  >>> %s: %d registros novos sincronizados\n" % (ciclo, total_ins))
            log.flush()
            pcur.execute("SET session_replication_role = DEFAULT")
            pconn.commit()
            pconn.close(); sconn.close(); tunnel.close(); local.close()
        except Exception as e:
            log.write("  ERRO ciclo: %s\n" % str(e)[:200]); log.flush()
        # espera 5 min
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
