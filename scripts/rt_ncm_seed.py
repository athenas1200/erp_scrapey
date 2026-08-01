# -*- coding: utf-8 -*-
"""RT_NCM_SEED - popula rt_regras_ncm com os NCM mais usados no ERP (dados reais).
Le os NCM de Movimento_Prod_Serv na replica e cria regras default parametrizaveis
(incidencia CBS/IBS = TRUE, credito 0) em rt_regras_ncm. Depois o fiscal ajusta
os percentuais de credito/beneficios via banco sem tocar no codigo.

Uso: python rt_ncm_seed.py [limite]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'

LIMITE = 200
if len(sys.argv) > 1:
    try: LIMITE = int(sys.argv[1])
    except Exception: pass


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(os.path.join(LOGDIR, 'rt_ncm_seed.log'), 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)


def main():
    from db_conn import connect, close_all
    from db_mem import connect as mconnect, close_all as mclose
    from psycopg2.extras import execute_values

    hoje = time.strftime('%Y-%m-%d')
    # 1. top NCM do ERP (janela de 90 dias)
    tr, lr, cr, cur = connect()
    try:
        cur.execute("""SELECT "NCM", COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "NCM" IS NOT NULL AND "NCM" <> ''
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => 90)
            GROUP BY "NCM" ORDER BY COUNT(*) DESC LIMIT %s""", (LIMITE,))
        top = cur.fetchall()
    finally:
        close_all(tr, lr, cr)

    # 2. carrega NCM ja registrados
    tm, lm, cm, mcur = mconnect()
    try:
        mcur.execute("SELECT ncm FROM rt_regras_ncm")
        ja = {r[0] for r in mcur.fetchall()}
        novos = [(str(ncm), True, True, 0, 0, None, 0, hoje, None) for ncm, _ in top if str(ncm) not in ja]
        if novos:
            execute_values(mcur, """INSERT INTO rt_regras_ncm
                (ncm, incide_cbs, incide_ibs, credito_cbs_percentual, credito_ibs_percentual,
                 nbs, reducao_base_percentual, vigencia_inicio, vigencia_fim)
                VALUES %s ON CONFLICT DO NOTHING""", novos)
        cm.commit()
        mcur.execute("SELECT COUNT(*) FROM rt_regras_ncm")
        total = mcur.fetchone()[0]
        print("rt_regras_ncm: %d novos -> %d total" % (len(novos), total))
        log("rt_regras_ncm: %d novos -> %d total" % (len(novos), total))
    finally:
        mclose(tm, lm, cm)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO rt_ncm_seed: %s\n%s" % (str(e)[:300], traceback.format_exc()))
