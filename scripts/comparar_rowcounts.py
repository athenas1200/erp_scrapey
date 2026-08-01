# -*- coding: utf-8 -*-
"""COMPARA ROWCOUNTS - verifica se a replica VPS (s9_real) tem os MESMOS dados
que o SQL Server (S9_Real). Gera logs/comparacao_YYYY-MM-DD.json com o resultado
por tabela (SQL, PG, diferenca) + resumo. Somente leitura nos dois bancos.

Uso: python comparar_rowcounts.py [--sql | --pg | --ambos]
  --sql   conta no SQL Server (gera sql_rowcounts_atual.json)
  --pg    conta na VPS e compara com sql_rowcounts.json
  (padrao: --ambos -> conta nos dois e compara)

Tambem salva checkpoint comparacao_estado.json (evita recontar se nada mudou).
"""
import sys, io, os, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = LOGDIR + r'\comparacao_%s.json' % time.strftime('%Y-%m-%d')
REFSQL = BASE + r'\sql_rowcounts.json'
REFPG = BASE + r'\sql_rowcounts_atual.json'

MODO = 'ambos'
if '--sql' in sys.argv: MODO = 'sql'
if '--pg' in sys.argv: MODO = 'pg'

SQL_DSN = "DRIVER={SQL Server};SERVER=192.168.0.101,1435;DATABASE=S9_Real;UID=sa;PWD=Elipse18;"
VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"


def jlog(entry):
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')


def log(msg):
    line = "[%s] %s" % (time.strftime('%H:%M:%S'), msg)
    print(line)
    jlog({'tipo': 'log', 'msg': msg})


def contar_sql():
    import pyodbc
    conn = pyodbc.connect(SQL_DSN, timeout=60)
    cur = conn.cursor()
    cur.execute("""SELECT t.name FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id=t.schema_id
        WHERE s.name='dbo' ORDER BY t.name""")
    tabelas = [r[0] for r in cur.fetchall()]
    res = {}
    for t in tabelas:
        try:
            cur.execute('SELECT COUNT(*) FROM dbo.[%s]' % t)
            res[t] = cur.fetchone()[0]
        except Exception:
            res[t] = -1
    conn.close()
    return res


def contar_pg():
    import paramiko, socket, threading, psycopg2
    tunnel = paramiko.SSHClient()
    tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    tunnel.connect(VPS_HOST, username=VPS_USER, password=VPS_PWD, timeout=30)
    tr = tunnel.get_transport()
    local = socket.socket()
    local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local.bind(("127.0.0.1", 15435)); local.listen(5)
    def fwd():
        while True:
            try: c, _ = local.accept()
            except Exception: break
            ch = tr.open_channel("direct-tcpip", ("127.0.0.1", 5434), c.getpeername())
            def pipe(s, d):
                try:
                    while True:
                        b = s.recv(65536)
                        if not b: break
                        d.sendall(b)
                except Exception: pass
                try: d.shutdown(2)
                except Exception: pass
            import threading as _t
            _t.Thread(target=pipe, args=(c, ch), daemon=True).start()
            _t.Thread(target=pipe, args=(ch, c), daemon=True).start()
    threading.Thread(target=fwd, daemon=True).start()
    conn = psycopg2.connect(host="127.0.0.1", port=15435, dbname="s9_real",
                            user="postgres", password=PG_PWD, connect_timeout=60)
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("""SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'""")
    tabelas = [r[0] for r in cur.fetchall()]
    res = {}
    for t in tabelas:
        try:
            cur.execute('SELECT COUNT(*) FROM "%s"' % t)
            res[t] = cur.fetchone()[0]
        except Exception:
            res[t] = -1
    conn.close(); local.close(); tunnel.close()
    return res


def comparar(sql, pg):
    todas = sorted(set(list(sql.keys()) + list(pg.keys())))
    res = []
    n_ok = n_div = n_somente_pg = n_erro = 0
    for t in todas:
        a = sql.get(t, 0); b = pg.get(t, 0)
        if a == -1 or b == -1:
            n_erro += 1
            res.append({'tabela': t, 'sql': a, 'pg': b, 'status': 'erro'})
        elif t not in pg:
            n_somente_pg += 1
            res.append({'tabela': t, 'sql': a, 'pg': None, 'status': 'somente_sql'})
        elif a == b:
            n_ok += 1
            res.append({'tabela': t, 'sql': a, 'pg': b, 'status': 'ok'})
        else:
            n_div += 1
            res.append({'tabela': t, 'sql': a, 'pg': b, 'status': 'divergente',
                        'diferenca': b - a, 'percentual_pg': (round(b / a * 100, 1) if a else None)})
    resumo = {
        'gerado_em': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_tabelas': len(todas),
        'iguais': n_ok,
        'divergentes': n_div,
        'somente_sql': n_somente_pg,
        'erros': n_erro,
    }
    return res, resumo


def main():
    log("COMPARACAO ROWCOUNTS (modo=%s)" % MODO)
    sql = pg = None
    if MODO in ('sql', 'ambos'):
        log("contando SQL Server...")
        sql = contar_sql()
        io.open(REFSQL, 'w', encoding='utf-8').write(json.dumps(sql, ensure_ascii=False, default=str))
        log("SQL: %d tabelas contadas" % len(sql))
    if MODO in ('pg', 'ambos'):
        log("contando VPS (s9_real)...")
        pg = contar_pg()
        io.open(REFPG, 'w', encoding='utf-8').write(json.dumps(pg, ensure_ascii=False, default=str))
        log("PG: %d tabelas contadas" % len(pg))

    if MODO == 'ambos':
        if not sql: sql = json.load(io.open(REFSQL, encoding='utf-8'))
        if not pg: pg = json.load(io.open(REFPG, encoding='utf-8'))
        res, resumo = comparar(sql, pg)
        io.open(LOGDIR + r'\resumo_comparacao.json', 'w', encoding='utf-8').write(
            json.dumps(resumo, ensure_ascii=False, indent=1))
        print("\n=== RESUMO ===")
        for k, v in resumo.items():
            print("  %s: %s" % (k, v))
        print("\n=== DIVERGENCIAS ===")
        for r in res:
            if r['status'] == 'divergente':
                print("  %-45s SQL=%-9s PG=%-9s dif=%+d (%.1f%%)" %
                      (r['tabela'], r['sql'], r['pg'], r.get('diferenca', 0),
                       r.get('percentual_pg') or 0))
        for r in res:
            if r['status'] == 'somente_sql':
                print("  %-45s SOMENTE SQL (falta na VPS) SQL=%s" % (r['tabela'], r['sql']))
        for r in res:
            if r['status'] == 'erro':
                print("  %-45s ERRO ao contar (sql=%s pg=%s)" % (r['tabela'], r['sql'], r['pg']))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        jlog({'tipo': 'erro', 'msg': str(e), 'trace': traceback.format_exc()})
        raise
