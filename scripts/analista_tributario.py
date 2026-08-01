# -*- coding: utf-8 -*-
"""ANALISTA TRIBUTARIO - inteligencia tributaria do ERP S9.
Analisa NCM, CEST, CFOP, CST/CSOSN, aliquotas e riscos fiscais a partir da
replica s9_real e grava o conhecimento em memory_fiscal + memory_business_rules
+ logs/metricas_diario.json (alimenta o email das 9h).

Descobre automaticamente:
  - NCM sem CEST quando obrigatorio (CST 10/30/70/90 - substituicao)
  - Produtos sem NCM / NCM duplicados / NCM invalidos
  - CFOP mais usados por tipo de operacao
  - Composicao de CST/CSOSN (ICMS/PIS/COFINS)
  - Aliquotas medias por CST
  - Impacto CBS/IBS (reforma tributaria)
Modo leitura apenas. Uso: python analista_tributario.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_tributario.log')

DIAS = 30
if len(sys.argv) > 1:
    try: DIAS = int(sys.argv[1])
    except Exception: pass

# CST que exigem CEST (substituicao tributaria ICMS)
CST_COM_CEST = {'10', '30', '70', '90'}


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


# ---------------- consultas ----------------
def ncm_produtos(cur):
    """Produtos com NCM: lista NCM duplicados, sem NCM, totais."""
    cur.execute("""SELECT "NCM", COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE "NCM" IS NOT NULL AND "NCM" <> ''
        GROUP BY "NCM" HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 15""")
    duplicados = cur.fetchall()
    cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE ("NCM" IS NULL OR "NCM" = '') AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""",
                (DIAS,))
    sem_ncm = cur.fetchone()[0]
    cur.execute("""SELECT COUNT(DISTINCT "NCM") FROM "Movimento_Prod_Serv"
        WHERE "NCM" IS NOT NULL AND "NCM" <> ''""")
    ncm_unicos = cur.fetchone()[0]
    return duplicados, sem_ncm, ncm_unicos


def cst_cfop_movimento(cur):
    """Composicao de CST/CSOSN e CFOP nos itens de movimento."""
    cur.execute("""SELECT "ICMS_CST_CSOSN", COUNT(*),
        COALESCE(AVG("ICMS_Normal_Percentual"),0),
        COALESCE(AVG("PIS_Normal_Percentual"),0),
        COALESCE(AVG("COFINS_Normal_Percentual"),0)
        FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY "ICMS_CST_CSOSN" ORDER BY COUNT(*) DESC LIMIT 20""", (DIAS,))
    cst = cur.fetchall()
    cur.execute("""SELECT "CFOP_NF", COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
          AND "CFOP_NF" IS NOT NULL AND "CFOP_NF" <> ''
        GROUP BY "CFOP_NF" ORDER BY COUNT(*) DESC LIMIT 15""", (DIAS,))
    cfop = cur.fetchall()
    return cst, cfop


def produtos_com_st_sem_cest(cur):
    """CST de ST mas sem CEST (risco fiscal)."""
    cst_list = "', '".join(sorted(CST_COM_CEST))
    cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE "ICMS_CST_CSOSN" IN ('%s')
          AND ("CEST" IS NULL OR "CEST" = '')""" % cst_list)
    return cur.fetchone()[0]


def impacto_cbs_ibs(cur):
    """Impacto da reforma: valores CBS/IBS vs ICMS/PIS/COFINS na janela."""
    cur.execute("""SELECT
        COALESCE(SUM("ICMS_Normal_Valor")+SUM("ICMS_Subst_Valor")+SUM("ICMS_Retido_Valor"),0) AS icms_total,
        COALESCE(SUM("PIS_Normal_Valor")+SUM("COFINS_Normal_Valor"),0) AS pis_cofins,
        COALESCE(SUM("CBS_Valor"),0) AS cbs,
        COALESCE(SUM("IBS_Valor"),0) AS ibs,
        COALESCE(SUM("Preco_Total_Sem_Desconto"),0) AS base
        FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
    return cur.fetchone()


def aliquotas_produto(cur):
    """Produtos com aliquota ICMS acima do padrao (anomalia)."""
    cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
          AND ("ICMS_Normal_Percentual" > 20 OR "ICMS_Normal_Percentual" < 0)""", (DIAS,))
    anomalia = cur.fetchone()[0]
    cur.execute("""SELECT COALESCE(AVG("ICMS_Normal_Percentual"),0),
        COALESCE(AVG("ICMS_Subst_Percentual"),0) FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
    return anomalia, cur.fetchone()


# ---------------- gravacao ----------------
def gravar_na_memoria(linhas_texto):
    from db_mem import connect, close_all
    from psycopg2.extras import execute_values
    tm, lm, cm, mcur = connect()
    try:
        hoje = time.strftime('%Y-%m-%d')
        dados = [(r[0], r[1], 0.85, hoje) for r in linhas_texto]
        if dados:
            execute_values(mcur, """INSERT INTO memory_business_rules
                (tabela, regra, significado, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""",
                           [(d[0], d[1], "regra tributaria", d[2], d[3]) for d in dados])
        cm.commit()
    finally:
        close_all(tm, lm, cm)

    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    hoje = time.strftime('%Y-%m-%d')
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista tributario")]
    itens.extend(["Analista tributario: " + t for t in linhas_texto])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))


def main():
    from db_conn import connect, close_all
    log("ANALISTA TRIBUTARIO iniciado (DIAS=%d)" % DIAS)
    tr, lr, cr, cur = connect()
    try:
        dup, sem_ncm, ncm_unicos = ncm_produtos(cur)
        cst, cfop = cst_cfop_movimento(cur)
        sem_cest = produtos_com_st_sem_cest(cur)
        icms_t, pis_cofins, cbs, ibs, base = impacto_cbs_ibs(cur)
        aliquota_anomalia, aliquotas = aliquotas_produto(cur)
    finally:
        close_all(tr, lr, cr)

    linhas = []
    linhas.append("NCM: %d codigos unicos; %d produtos sem NCM; %d NCM duplicados" %
                  (ncm_unicos, sem_ncm, len(dup)))
    if dup:
        linhas.append("  NCM duplicados (top): " + "; ".join("%s x%d" % (r[0], r[1]) for r in dup[:5]))
    linhas.append("Risco: %d itens com CST de substituicao sem CEST" % sem_cest)
    if cst:
        linhas.append("CST/CSOSN mais usados: " + "; ".join(
            "%s x%d (ICMS %.1f%%, PIS %.1f%%, COFINS %.1f%%)" % (r[0], r[1], _num(r[2]), _num(r[3]), _num(r[4]))
            for r in cst[:6]))
    if cfop:
        linhas.append("CFOP mais usados: " + "; ".join("%s x%d" % (r[0], r[1]) for r in cfop[:6]))
    linhas.append("Aliquotas medias: ICMS normal %.1f%%, ICMS-ST %.1f%%, anomalias (>20%% ou negativas): %d" %
                  (_num(aliquotas[0]), _num(aliquotas[1]), aliquota_anomalia))
    icms_t, pis_cofins, cbs, ibs, base = _num(icms_t), _num(pis_cofins), _num(cbs), _num(ibs), _num(base)
    if base:
        linhas.append("Carga tributaria (%d dias): ICMS R$ %.0f (%.2f%%), PIS+COFINS R$ %.0f (%.2f%%), "
                      "base R$ %.0f" % (DIAS, icms_t, icms_t / base * 100, pis_cofins, pis_cofins / base * 100, base))
    if (cbs + ibs) > 0:
        linhas.append("Reforma tributaria (CBS/IBS): CBS R$ %.0f + IBS R$ %.0f (registrados no ERP)" % (cbs, ibs))

    log("Analista tributario: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)
    gravar_na_memoria(linhas)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_tributario: %s\n%s" % (str(e)[:300], traceback.format_exc()))
