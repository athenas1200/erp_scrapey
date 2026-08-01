# -*- coding: utf-8 -*-
"""RESUMO DE TABELAS ALTERADAS - verifica cadastros via Data_Alteracao.
Roda em loop silencioso (sem output no console).
A cada ciclo (5 min):
- Para cada tabela de cadastro com coluna Data_Alteracao, compara o max()
  do SQL Server com o max() do PostgreSQL.
- Se houver registros alterados no SQL mais recentes que o PG, conta:
    alterados  = total de linhas no SQL com Data_Alteracao > max(PG)
    novos      = desses, PKs que ainda NAO existem no PG (insert pendente)
    atualizados= desses, PKs que ja existem no PG (update pendente)
- Grava resumo diario em JSON: logs/resumo_AAAA-MM-DD.json
Nada e impresso no console.
"""
import io, json, os, time, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sync_silencioso.py'))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

SQL_DSN = sync.SQL_DSN
BASE = sync.BASE
LOGDIR = sync.LOGDIR
INTERVAL = 300
BATCH = 1000

def tem_data_alteracao(scur, t):
    r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
    return r[0] > 0

def get_pk(scur, t):
    pk = scur.execute("""SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE c
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc ON c.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
        WHERE c.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='PRIMARY KEY'""", t).fetchall()
    return pk[0][0] if pk else None

def classificar(novos_pks, pcur, pconn, t, pkcol):
    """Retorna (n_novos, n_atualizados) comparando PKs contra PG."""
    if not novos_pks:
        return 0, 0
    n_novos = 0
    n_atualizados = 0
    for i in range(0, len(novos_pks), BATCH):
        chunk = novos_pks[i:i+BATCH]
        pcur.execute('SELECT "%s" FROM "%s" WHERE "%s" = ANY(%%s)'
                     % (pkcol, t, pkcol), (list(chunk),))
        existe = set(r[0] for r in pcur.fetchall())
        for pk in chunk:
            if pk in existe:
                n_atualizados += 1
            else:
                n_novos += 1
    return n_novos, n_atualizados

def gerar_resumo(scur, pcur, pconn):
    resumo = []
    for t in sync.tabelas:
        if not tem_data_alteracao(scur, t):
            continue
        pkcol = get_pk(scur, t)
        if not pkcol:
            continue
        try:
            pcur.execute('SELECT max("Data_Alteracao") FROM "%s"' % t)
            pg_max = pcur.fetchone()[0]
            if pg_max is None:
                continue
            scur.execute("""SELECT %s FROM dbo.[%s]
                WHERE Data_Alteracao > ?""" % (pkcol, t), (pg_max,))
            novos_pks = [r[0] for r in scur.fetchall()]
            if not novos_pks:
                continue
            n_novos, n_atualizados = classificar(novos_pks, pcur, pconn, t, pkcol)
            resumo.append({
                'tabela': t,
                'alterados': len(novos_pks),
                'novos': n_novos,
                'atualizados': n_atualizados,
                'data_ultimo_pg': str(pg_max),
            })
        except Exception as e:
            pass
    return resumo

def gravar(resumo):
    dia = time.strftime('%Y-%m-%d')
    path = LOGDIR + r'\resumo_%s.json' % dia
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'data': dia,
            'gerado_em': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_tabelas_alteradas': len(resumo),
            'tabelas': resumo,
        }, f, ensure_ascii=False, indent=2)

def main():
    while True:
        try:
            sconn = __import__('pyodbc').connect(SQL_DSN, timeout=30)
            scur = sconn.cursor()
            tunnel, local = sync.open_tunnel()
            pconn = __import__('psycopg2').connect(
                host="127.0.0.1", port=15434, dbname="s9_real",
                user="postgres", password=sync.PG_PWD, connect_timeout=30)
            pcur = pconn.cursor()
            resumo = gerar_resumo(scur, pcur, pconn)
            gravar(resumo)
            pconn.close(); sconn.close(); tunnel.close(); local.close()
        except Exception:
            pass
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
