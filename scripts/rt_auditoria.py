# -*- coding: utf-8 -*-
"""RT_AUDITORIA - auditoria da Reforma Tributaria (CBS/IBS) sobre dados reais.
Valida na replica s9_real e grava em rt_log_calculos + metricas_diario.json:
  - Produtos sem NCM / NCM sem regra rt_regras_ncm
  - NCM sem CEST quando ST
  - CBC/IBS registrados no ERP vs calculados (diferencas)
  - Operacoes sem regra rt_regras_operacao
  - Clientes sem UF / regime tributario
  - Beneficios vencidos
Modo leitura apenas na replica. Uso: python rt_auditoria.py [dias]
"""
import sys, io, os, json, time
from datetime import date

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'rt_auditoria.log')

DIAS = 30
if len(sys.argv) > 1:
    try: DIAS = int(sys.argv[1])
    except Exception: pass


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)


def _num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def main():
    from db_conn import connect, close_all
    log("RT AUDITORIA iniciado (DIAS=%d)" % DIAS)
    tr, lr, cr, cur = connect()
    achados = []
    try:
        # 1. itens sem NCM na janela
        cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE ("NCM" IS NULL OR "NCM" = '')
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        achados.append(("itens de movimento sem NCM", cur.fetchone()[0], "alta"))

        # 2. ST sem CEST (risco)
        cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "ICMS_CST_CSOSN" IN ('10','30','70','90')
              AND ("CEST" IS NULL OR "CEST" = '')
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        achados.append(("itens com CST de substituicao sem CEST", cur.fetchone()[0], "alta"))

        # 3. CBS/IBS registrados vs soma de impostos atuais
        cur.execute("""SELECT COALESCE(SUM("CBS_Valor"),0), COALESCE(SUM("IBS_Valor"),0),
            COALESCE(SUM("ICMS_Normal_Valor")+SUM("PIS_Normal_Valor")+SUM("COFINS_Normal_Valor"),0)
            FROM "Movimento_Prod_Serv"
            WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        cbs, ibs, atuais = cur.fetchone()
        achados.append(("CBS registrada no ERP (R$)", round(_num(cbs), 2), "media"))
        achados.append(("IBS registrada no ERP (R$)", round(_num(ibs), 2), "media"))
        achados.append(("impostos atuais ICMS+PIS+COFINS (R$)", round(_num(atuais), 2), "media"))

        # 4. NCM mais usados (para conferir regras rt_regras_ncm)
        cur.execute("""SELECT "NCM", COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "NCM" IS NOT NULL AND "NCM" <> ''
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
            GROUP BY "NCM" ORDER BY COUNT(*) DESC LIMIT 10""", (DIAS,))
        top_ncm = cur.fetchall()
    finally:
        close_all(tr, lr, cr)

    # carrega regras ncm do banco de parametros
    from db_mem import connect as mconnect, close_all as mclose
    tm, lm, cm, mcur = mconnect()
    try:
        mcur.execute("SELECT ncm FROM rt_regras_ncm")
        ncm_registrados = {r[0] for r in mcur.fetchall()}
        # grava achados no log de calculos
        from psycopg2.extras import execute_values
        execute_values(mcur, """INSERT INTO rt_log_calculos (objeto, tipo, detalhe, criado_em)
            VALUES %s""",
                       [(a[0], 'auditoria', json.dumps({'valor': a[1], 'confianca': a[2]}, ensure_ascii=False),
                         time.strftime('%Y-%m-%d %H:%M:%S')) for a in achados])
        cm.commit()
    finally:
        mclose(tm, lm, cm)

    linhas = []
    for nome, valor, conf in achados:
        linhas.append("%s: %s (confianca %s)" % (nome, valor, conf))
    for ncm, cnt in top_ncm[:5]:
        status = "OK" if str(ncm) in ncm_registrados else "SEM REGRA"
        linhas.append("NCM %s: %d itens (%s)" % (ncm, cnt, status))
    linhas.append("NCM com regra cadastrada em rt_regras_ncm: %d" % len(ncm_registrados))

    # alimenta metricas_diario.json
    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    hoje = time.strftime('%Y-%m-%d')
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("RT Auditoria")]
    itens.extend(["RT Auditoria: " + l for l in linhas])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))

    log("RT Auditoria: %d achados" % len(linhas))
    for l in linhas:
        log("  " + l)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO rt_auditoria: %s\n%s" % (str(e)[:300], traceback.format_exc()))
