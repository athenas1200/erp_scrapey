# -*- coding: utf-8 -*-
"""ENRIQUECEDOR DE METRICAS FISCAIS - calcula metricas agregadas de impostos
a partir da replica s9_real e grava em memory_fiscal (db postgres da VPS) +
logs/metricas_diario.json (alimenta o email das 9h).

Metricas calculadas (janela de hoje / ultimos N dias):
  - ICMS normal/retido/ST: base, percentual medio, valor total
  - ICMS substituicao tributaria: MVA, percentual, base, valor
  - PIS/COFINS: normal + substituicao
  - IBS/CBS (reforma tributaria): valores por item
  - Desconto medio diario: percentual e valor
  - Composicao CST/CSOSN e CFOP mais usados
  - Regras de Classe_Imposto_Operacao (como os calculos sao feitos)

Uso: python metricas_fiscais.py [dias]   (padrao 1 = hoje; 30 = mes)
Agendado pelo vigia a cada 6h.
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'metricas_fiscais.log')

DIAS = 1
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


def agregar_movimento(cur):
    """Agregados de impostos em Movimento_Prod_Serv (replica, janela de dias)."""
    import psycopg2
    desde = (time.time() - DIAS * 86400)
    cur.execute("SET statement_timeout = 300000")
    # janela via Data_Efetivacao (ou Data_Alteracao) na replica (so tem 2026+)
    cur.execute("""
      SELECT
        COUNT(*) AS itens,
        COALESCE(SUM("Preco_Total_Sem_Desconto"),0) AS faturamento_sem_desc,
        COALESCE(SUM("Preco_Total_Com_Desconto"),0) AS faturamento_com_desc,
        COALESCE(SUM("Desconto_Valor"),0) AS desconto_valor,
        AVG("Desconto_Percentual") AS desconto_perc_medio,
        COALESCE(SUM("ICMS_Normal_Valor"),0) AS icms_normal,
        COALESCE(SUM("ICMS_Subst_Valor"),0) AS icms_st,
        COALESCE(SUM("ICMS_Retido_Valor"),0) AS icms_retido,
        COALESCE(SUM("ICMS_Substituto_Valor"),0) AS icms_substituto,
        AVG("ICMS_Subst_Percentual_MVA") AS mva_medio,
        AVG("ICMS_Subst_Percentual") AS icms_st_perc_medio,
        COALESCE(SUM("PIS_Normal_Valor"),0) AS pis,
        COALESCE(SUM("PIS_Subst_Valor"),0) AS pis_st,
        COALESCE(SUM("COFINS_Normal_Valor"),0) AS cofins,
        COALESCE(SUM("COFINS_Subst_Valor"),0) AS cofins_st,
        COALESCE(SUM("IPI_Valor"),0) AS ipi,
        COALESCE(SUM("ISS_Valor"),0) AS iss,
        COALESCE(SUM("FCP_ST_Valor"),0) AS fcp_st,
        COALESCE(SUM("CBS_Valor"),0) AS cbs,
        COALESCE(SUM("IBS_Valor"),0) AS ibs,
        COALESCE(SUM("ICMS_Efetivo_Valor"),0) AS icms_efetivo,
        COUNT(CASE WHEN "ICMS_Subst_Valor" > 0 THEN 1 END) AS itens_com_st,
        COUNT(CASE WHEN "Desconto_Valor" > 0 THEN 1 END) AS itens_com_desconto
      FROM "Movimento_Prod_Serv"
      WHERE "Data_Efetivacao_Estoque" >= (CURRENT_TIMESTAMP - make_interval(days => %s))
        AND ("Preco_Total_Sem_Desconto" IS NOT NULL OR "ICMS_Normal_Valor" IS NOT NULL OR "ICMS_Subst_Valor" IS NOT NULL)
    """, (DIAS,))
    return cur.fetchone()


def top_cst_cfop(cur):
    """Composicao de CST/CSOSN e CFOP mais usados na janela."""
    desde = time.strftime('%Y-%m-%d') if DIAS <= 1 else (time.strftime('%Y-%m-%d', time.gmtime(time.time() - DIAS * 86400)))
    out = {}
    for col, rotulo in [('ICMS_CST_CSOSN', 'CST/CSOSN'), ('CFOP_NF', 'CFOP')]:
        try:
            cur.execute("""SELECT "%%s" AS v, COUNT(*) FROM "Movimento_Prod_Serv"
                WHERE "%%s" IS NOT NULL AND "%%s" <> '' AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %%s)
                GROUP BY "%%s" ORDER BY COUNT(*) DESC LIMIT 8""" % (col, col, col, col), (DIAS,))
            out[rotulo] = [(str(r[0]), int(r[1])) for r in cur.fetchall()]
        except Exception:
            out[rotulo] = []
    return out


def regras_calculo(cur):
    """Como os calculos sao feitos: percentuais por Classe_Imposto_Operacao."""
    cur.execute("""SELECT "ICMS_CST_CSOSN",
        COALESCE(AVG("ICMS_Percentual_Norm"),0), COALESCE(AVG("PIS_Percentual_Norm"),0),
        COALESCE(AVG("COFINS_Percentual_Norm"),0),
        COUNT(*)
        FROM "Classe_Imposto_Operacao"
        GROUP BY "ICMS_CST_CSOSN" ORDER BY COUNT(*) DESC LIMIT 12""")
    return [(str(r[0]), _num(r[1]), _num(r[2]), _num(r[3]), int(r[4])) for r in cur.fetchall()]


def gravar_memory_fiscal(cur, hoje, dados):
    from psycopg2.extras import execute_values
    rows = []
    for regra in dados['regras']:
        rows.append((regra[0], 'ICMS_CST_CSOSN=' + str(regra[0]),
                     'CST/CSOSN', None, None, None, None,
                     regra[1], regra[2], regra[3], None, None, None, hoje))
    if rows:
        execute_values(cur, """INSERT INTO memory_fiscal
            (produto_codigo, ncm, cfop, cst, csosn, cest, icms, ipi, pis, cofins, iss, ibs, cbs, data)
            VALUES %s ON CONFLICT DO NOTHING""", rows)


def main():
    from db_conn import connect as c_conn, close_all as c_close
    from db_mem import connect as m_conn, close_all as m_close
    log("Metricas fiscais: DIAS=%d" % DIAS)
    hoje = time.strftime('%Y-%m-%d')

    tr, lr, cr, cur = c_conn()
    try:
        agg = agregar_movimento(cur)
        cst = top_cst_cfop(cur)
        regras = regras_calculo(cur)
    finally:
        c_close(tr, lr, cr)

    itens, fat_sd, fat_cd, desc_v, desc_p, icms_n, icms_st, icms_ret, icms_sub, mva, \
        st_perc, pis, pis_st, cofins, cofins_st, ipi, iss, fcp_st, cbs, ibs, icms_ef, \
        itens_st, itens_desc = agg

    fat_sd, fat_cd, desc_v, desc_p = _num(fat_sd), _num(fat_cd), _num(desc_v), _num(desc_p)
    icms_n, icms_st, icms_ret, icms_sub = _num(icms_n), _num(icms_st), _num(icms_ret), _num(icms_sub)
    pis, pis_st, cofins, cofins_st = _num(pis), _num(pis_st), _num(cofins), _num(cofins_st)
    ipi, iss, fcp_st, cbs, ibs, icms_ef = _num(ipi), _num(iss), _num(fcp_st), _num(cbs), _num(ibs), _num(icms_ef)

    # grava na memoria
    tm, lm, cm, mcur = m_conn()
    try:
        gravar_memory_fiscal(mcur, hoje, {'regras': regras})
        cm.commit()
    finally:
        m_close(tm, lm, cm)

    # monta texto de metricas
    linhas = [
        "Metricas fiscais (%s, %d dias): %d itens de movimento, faturamento R$ %.2f (desconto medio %.2f%%, R$ %.2f)" %
        (hoje, DIAS, itens, fat_cd, desc_p, desc_v),
        "  ICMS normal R$ %.2f | ST R$ %.2f | retido R$ %.2f | substituto R$ %.2f | efetivo R$ %.2f" %
        (icms_n, icms_st, icms_ret, icms_sub, icms_ef),
        "  Substituicao tributaria: %d itens com ST, MVA medio %.2f%%, ICMS-ST %.2f%%, FCP-ST R$ %.2f" %
        (itens_st, _num(mva), st_perc, fcp_st),
        "  PIS R$ %.2f (+ST %.2f) | COFINS R$ %.2f (+ST %.2f) | IPI R$ %.2f | ISS R$ %.2f" %
        (pis, pis_st, cofins, cofins_st, ipi, iss),
        "  IBS R$ %.2f | CBS R$ %.2f (reforma tributaria)" % (ibs, cbs),
        "  Descontos: %d itens com desconto (%.0f%% do total)" % (itens_desc, (itens_desc * 100.0 / itens) if itens else 0),
    ]
    for rotulo, lista in cst.items():
        if lista:
            top = ', '.join("%s x%d" % (v, n) for v, n in lista[:5])
            linhas.append("  %s mais usados: %s" % (rotulo, top))
    linhas.append("  Regras de calculo (Classe_Imposto_Operacao): %d classes" % len(regras))
    for cst_code, ic, pisr, cofr, n in regras[:5]:
        linhas.append("    CST/CSOSN %s: ICMS %.2f%% | PIS %.2f%% | COFINS %.2f%% (%d classes)" %
                      (cst_code, ic, pisr, cofr, n))

    # adiciona ao metricas_diario.json (data de hoje)
    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    hoje_itens = d.get(hoje, [])
    hoje_itens = [x for x in hoje_itens if not x.startswith("Metricas fiscais")]
    hoje_itens.extend(linhas)
    d[hoje] = hoje_itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))

    log("Metricas fiscais calculadas: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO metricas_fiscais: %s\n%s" % (str(e)[:300], traceback.format_exc()))
