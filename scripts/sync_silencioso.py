# -*- coding: utf-8 -*-
"""SINCRONIZADOR SILENCIOSO - SQL Server -> PostgreSQL (VPS)
Roda em loop a cada 5 minutos, sem output no console.
- Detecta inserts (novos por PK) e replica.
- Analisa a ESTRUTURA a cada ciclo: se o SQL Server tiver colunas novas,
  adiciona automaticamente no PostgreSQL (ALTER TABLE ADD COLUMN) e tenta
  corrigir tipo caso tenha mudado.
- Log completo diário em JSON: logs/sync_YYYY-MM-DD.json
- Também mantém sync_log.txt (texto, para consulta rápida).
"""
import pyodbc, psycopg2, paramiko, threading, socket, json, time, io, os
from decimal import Decimal
from psycopg2.extras import execute_values

SQL_DSN = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
INTERVAL = 300
BATCH = 2000

os.makedirs(LOGDIR, exist_ok=True)

cat = json.load(io.open(BASE + r'\mig_cat.json', encoding='utf-8'))
rows = json.load(io.open(BASE + r'\sql_rowcounts.json', encoding='utf-8'))
tabelas = [t for t in cat if cat.get(t) != 'skip' and rows.get(t, 0) > 0
           and not t.endswith('_Fotos') and t != 'Funcionarios_Fotos']
tabelas.sort()

TYPE_MAP = {
    "int": "INTEGER", "bigint": "BIGINT", "smallint": "SMALLINT", "tinyint": "SMALLINT",
    "bit": "BOOLEAN", "money": "NUMERIC(19,4)", "smallmoney": "NUMERIC(10,4)",
    "datetime": "TIMESTAMP", "datetime2": "TIMESTAMP", "smalldatetime": "TIMESTAMP",
    "date": "DATE", "time": "TIME", "float": "DOUBLE PRECISION", "real": "REAL",
    "text": "TEXT", "ntext": "TEXT", "uniqueidentifier": "UUID",
    "binary": "BYTEA", "varbinary": "BYTEA", "image": "BYTEA",
    "xml": "TEXT", "sql_variant": "TEXT", "timestamp": "BYTEA",
}

def conv_type(dt, length, prec, scale):
    dt = dt.lower()
    if dt in ("varchar", "nvarchar", "char", "nchar"):
        if length is None or length <= 0:
            return "TEXT"
        return "%s(%d)" % ("VARCHAR" if dt.startswith("v") else "CHAR", length)
    if dt == "decimal" or dt == "numeric":
        if prec and scale is not None:
            return "NUMERIC(%d,%d)" % (prec, scale)
        return "NUMERIC"
    if dt in TYPE_MAP:
        return TYPE_MAP[dt]
    return "TEXT"

def norm_type(s):
    s = s.lower().replace(' ', '').replace('withouttimezone', '')
    s = s.replace('charactervarying', 'varchar').replace('character', 'char')
    return s

def pg_type_of_row(r):
    dt = (r[1] or '').lower()
    length = r[2]; prec = r[3]; scale = r[4]
    if dt in ('character varying', 'character'):
        base = 'varchar' if dt == 'character varying' else 'char'
        if length and length > 0:
            return '%s(%d)' % (base, length)
        return 'text'
    if dt in ('numeric', 'decimal'):
        if prec is not None and scale is not None:
            return 'numeric(%d,%d)' % (prec, scale)
        return 'numeric'
    if 'timestamp' in dt:
        return 'timestamp'
    if dt == 'time without time zone':
        return 'time'
    return dt

# ---------- LOG JSON DIARIO ----------
def jlog(entry):
    entry = dict(entry)
    entry.setdefault('hora', time.strftime('%H:%M:%S'))
    entry.setdefault('data', time.strftime('%Y-%m-%d'))
    dia = entry['data']
    path = LOGDIR + r'\sync_%s.json' % dia
    with io.open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')

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

# ---------- SYNC DE ESTRUTURA ----------
def sync_structure(scur, pcur, pconn, t, log):
    scur.execute("""SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
        NUMERIC_PRECISION, NUMERIC_SCALE FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME=? AND TABLE_SCHEMA='dbo' ORDER BY ORDINAL_POSITION""", t)
    s_cols = scur.fetchall()
    pcur.execute("""SELECT column_name, data_type, character_maximum_length,
        numeric_precision, numeric_scale FROM information_schema.columns
        WHERE table_name=%s AND table_schema='public'""", (t,))
    pg = {r[0].lower(): r for r in pcur.fetchall()}
    for r in s_cols:
        name, dt, length, prec, scale = r
        pgtype = conv_type(dt, length, prec, scale)
        key = name.lower()
        if key not in pg:
            short = name[:63].lower()
            if short in pg:
                # PG trunca nomes em 63 chars (NAMEDATALEN) - ja existe truncado
                continue
            try:
                pcur.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (t, name, pgtype))
                pconn.commit()
                msg = '[%s] ESTRUTURA ADD %s.%s %s' % (time.strftime('%H:%M:%S'), t, name, pgtype)
                log.write(msg + '\n'); log.flush()
                jlog({'tipo': 'estrutura', 'acao': 'ADD', 'tabela': t, 'coluna': name, 'tipo_pg': pgtype})
            except Exception as e:
                pconn.rollback()
                log.write('[%s] ESTRUTURA ERRO ADD %s.%s: %s\n' % (time.strftime('%H:%M:%S'), t, name, str(e)[:120])); log.flush()
                jlog({'tipo': 'estrutura', 'acao': 'ADD_ERRO', 'tabela': t, 'coluna': name, 'msg': str(e)[:200]})
        else:
            atual = pg_type_of_row(pg[key])
            if norm_type(atual) != norm_type(pgtype):
                try:
                    pcur.execute('ALTER TABLE "%s" ALTER COLUMN "%s" TYPE %s' % (t, name, pgtype))
                    pconn.commit()
                    msg = '[%s] ESTRUTURA ALTER %s.%s %s->%s' % (time.strftime('%H:%M:%S'), t, name, atual, pgtype)
                    log.write(msg + '\n'); log.flush()
                    jlog({'tipo': 'estrutura', 'acao': 'ALTER', 'tabela': t, 'coluna': name,
                          'tipo_antes': atual, 'tipo_depois': pgtype})
                except Exception as e:
                    pconn.rollback()
                    log.write('[%s] ESTRUTURA ERRO ALTER %s.%s: %s\n' % (time.strftime('%H:%M:%S'), t, name, str(e)[:120])); log.flush()
                    jlog({'tipo': 'estrutura', 'acao': 'ALTER_ERRO', 'tabela': t, 'coluna': name, 'msg': str(e)[:200]})

# ---------- SYNC DE DADOS ----------
def sync_one(scur, pcur, pconn, t, log):
    pcur.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_name=%s AND table_schema='public' ORDER BY ordinal_position""", (t,))
    cols = [r[0] for r in pcur.fetchall()]
    if not cols:
        return
    s_cols = [r[0] for r in scur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME=? AND TABLE_SCHEMA='dbo' ORDER BY ORDINAL_POSITION""", t).fetchall()]
    s_set = {x.lower(): x for x in s_cols}
    cols = [c for c in cols if c.lower() in s_set]
    if not cols:
        return
    colnames = ', '.join('"%s"' % c for c in cols)

    is_mov = cat.get(t) == 'mov'
    tem_da = False
    if is_mov:
        r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
        tem_da = r[0] > 0
        if not tem_da:
            return

    pk = scur.execute("""SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE c
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc ON c.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
        WHERE c.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='PRIMARY KEY'""", t).fetchall()
    pkcol = pk[0][0] if pk else cols[0]

    where_base = ""
    if is_mov:
        where_base = "Data_Alteracao >= '2026-01-01' AND Data_Alteracao < '2027-01-01'"

    tp = scur.execute("SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=? AND COLUMN_NAME=?", t, pkcol).fetchone()
    numeric = tp is not None and tp[0] in ('int','bigint','smallint','tinyint','decimal','numeric','money','smallmoney','real','float','bit')

    if numeric:
        pcur.execute('SELECT coalesce(max("%s"), 0) FROM "%s"' % (pkcol, t))
        max_pg = pcur.fetchone()[0]
        q = 'SELECT %s FROM dbo.[%s] WHERE %s > ?' % (', '.join(cols), t, pkcol)
        params = [max_pg]
    else:
        pcur.execute('SELECT max(("%s")::text) FROM "%s"' % (pkcol, t))
        m = pcur.fetchone()[0]
        max_pg = m if m is not None else ''
        q = 'SELECT %s FROM dbo.[%s] WHERE CAST(%s AS NVARCHAR(4000)) > ?' % (', '.join(cols), t, pkcol)
        params = [max_pg]
    if where_base:
        q += ' AND %s' % where_base
    scur.execute(q, params)
    novos = scur.fetchall()
    if not novos:
        return
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
            msg = "[%s] +%d %s" % (time.strftime('%H:%M:%S'), len(data), t)
            log.write(msg + '\n'); log.flush()
            jlog({'tipo': 'salvo', 'tabela': t, 'registros': len(data)})
        except Exception as e:
            pconn.rollback()
            log.write("[%s] ERRO %s: %s\n" % (time.strftime('%H:%M:%S'), t, str(e)[:120])); log.flush()
            jlog({'tipo': 'erro', 'tabela': t, 'msg': str(e)[:300]})

def main():
    log = io.open(BASE + r'\sync_log.txt', 'a', encoding='utf-8')
    log.write("=== SYNC SILENCIOSO INICIADO %s ===\n" % time.strftime('%Y-%m-%d %H:%M:%S'))
    log.flush()
    jlog({'tipo': 'inicio', 'msg': 'sincronizador iniciado'})
    while True:
        try:
            sconn = pyodbc.connect(SQL_DSN, timeout=30)
            scur = sconn.cursor()
            tunnel, local = open_tunnel()
            pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real", user="postgres", password=PG_PWD, connect_timeout=30)
            pconn.autocommit = False
            pcur = pconn.cursor()
            pcur.execute("SET session_replication_role = replica")
            pconn.commit()
            jlog({'tipo': 'ciclo', 'msg': 'ciclo iniciado'})
            for t in tabelas:
                try:
                    sync_structure(scur, pcur, pconn, t, log)
                    sync_one(scur, pcur, pconn, t, log)
                except Exception as e:
                    pconn.rollback()
                    log.write("[%s] ERRO %s: %s\n" % (time.strftime('%H:%M:%S'), t, str(e)[:120])); log.flush()
                    jlog({'tipo': 'erro', 'tabela': t, 'msg': str(e)[:300]})
            pcur.execute("SET session_replication_role = DEFAULT")
            pconn.commit()
            pconn.close(); sconn.close(); tunnel.close(); local.close()
            jlog({'tipo': 'ciclo', 'msg': 'ciclo concluido'})
        except Exception as e:
            log.write("[%s] ERRO CICLO: %s\n" % (time.strftime('%H:%M:%S'), str(e)[:200])); log.flush()
            jlog({'tipo': 'erro', 'msg': 'erro no ciclo: ' + str(e)[:300]})
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
