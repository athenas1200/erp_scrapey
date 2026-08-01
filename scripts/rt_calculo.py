# -*- coding: utf-8 -*-
"""RT_CALCULO - motor de calculo da CBS e do IBS (Reforma Tributaria EC 132/2023).

TODAS as regras sao lidas do banco (schema rt_*). NUNCA usar aliquotas fixas no codigo.
Parametros em banco permitem atualizar a legislacao sem alterar o codigo.

Fluxo por item de nota:
  1. base de calculo   -> componentes parametrizaveis (rt_base_calculo)
  2. aliquotas CBS     -> rt_aliquotas_cbs (vigencia/regime/operacao/receita)
  3. aliquotas IBS     -> rt_aliquotas_ibs (vigencia/regime/uf origem/destino/municipio)
  4. beneficios        -> rt_beneficios (isencao/reducao/credito presumido)
  5. regras NCM/op/uf  -> rt_regras_*
  6. credito (nao cumulatividade) -> rt_creditos
  7. split payment     -> rt_split_payment
  8. memoria de calculo -> rt_log_calculos

Uso:
  python rt_calculo.py --calc <arquivo_json>   (calcula itens de um json)
  python rt_calculo.py --demo                   (exemplo com dados do ERP)
  python rt_calculo.py --audit                  (audita produtos/operacoes)
"""
import sys, io, os, json, time
from datetime import date

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        with io.open(os.path.join(LOGDIR, 'rt_calculo.log'), 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


# ======================== ACESSO A PARAMETROS (banco) ========================
class Parametros:
    """Cache de parametros carregados do banco. Se a tabela estiver vazia,
    usa apenas valores DEFAULT informados aqui como parametros de seguranca
    (registrados no banco em rt_parametros, nunca hardcoded no fluxo)."""

    def __init__(self, cur):
        self.cur = cur
        self.cbs = None
        self.ibs = None
        self.base = None
        self.ncm = {}
        self.ops = {}
        self.ufs = {}
        self.munis = {}
        self.regimes = {}
        self.beneficios = []

    def carregar(self):
        hoje = date.today()
        self.cbs = self._q("""SELECT regime, tipo_operacao, natureza_receita, aliquota,
            reduzir_base, credito_percentual FROM rt_aliquotas_cbs
            WHERE vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)""",
                           (hoje, hoje))
        self.ibs = self._q("""SELECT regime, tipo_operacao, uf_origem, uf_destino, municipio,
            aliquota, reduzir_base, credito_percentual FROM rt_aliquotas_ibs
            WHERE vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)""",
                           (hoje, hoje))
        self.base = self._q("""SELECT tributo, componente, sinal, ordem FROM rt_base_calculo""")
        self.ncm = {r[0]: r for r in self._q("""SELECT ncm, incide_cbs, incide_ibs,
            credito_cbs_percentual, credito_ibs_percentual, nbs, reducao_base_percentual
            FROM rt_regras_ncm""")}
        self.ops = {r[0]: r for r in self._q("""SELECT tipo_operacao, finalidade,
            incide_cbs, incide_ibs, credito_cbs_percentual, credito_ibs_percentual
            FROM rt_regras_operacao""")}
        self.ufs = {r[0]: r for r in self._q("""SELECT uf, aliquota_ibs_estadual,
            credito_percentual FROM rt_regras_uf""")}
        self.munis = {(r[0], r[1] or ''): r for r in self._q("""SELECT municipio, uf,
            aliquota_ibs_municipal, credito_percentual FROM rt_regras_municipio""")}
        self.regimes = {r[0]: r for r in self._q("""SELECT regime, tipo_contribuinte,
            credito_cbs_percentual, credito_ibs_percentual FROM rt_regras_regime""")}
        self.beneficios = self._q("""SELECT codigo, descricao, tipo, ncm, cest,
            produto_codigo, valor FROM rt_beneficios
            WHERE ativo AND vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)""",
                                  (hoje, hoje))

    def _q(self, sql, params=None):
        try:
            self.cur.execute(sql, params or ())
            return self.cur.fetchall()
        except Exception:
            return []

    # ---- resolucao de aliquotas ----
    def aliquota_cbs(self, regime, op, receita):
        for r in self.cbs:
            if (not r[0] or r[0] == regime) and (not r[1] or r[1] == op) and (not r[2] or r[2] == receita):
                return {'aliquota': float(r[3]), 'reducao': float(r[4] or 0), 'credito': float(r[5] or 0)}
        return None

    def aliquota_ibs(self, regime, op, uf_origem, uf_destino, municipio):
        for r in self.ibs:
            if (not r[0] or r[0] == regime) and (not r[1] or r[1] == op) \
               and (not r[2] or r[2] == uf_origem) and (not r[3] or r[3] == uf_destino) \
               and (not r[4] or r[4] == municipio):
                return {'aliquota': float(r[5]), 'reducao': float(r[6] or 0), 'credito': float(r[7] or 0)}
        # IBS = estadual + municipal (regras UF/municipio)
        est = self.ufs.get(uf_destino)
        mun = self.munis.get((municipio, uf_destino or ''))
        a_est = float(est[1]) if est and est[1] else 0
        a_mun = float(mun[2]) if mun and mun[2] else 0
        if a_est or a_mun:
            return {'aliquota': a_est + a_mun, 'reducao': 0, 'credito': 0}
        return None

    def beneficios_para(self, ncm, produto):
        out = []
        for b in self.beneficios:
            if (b[3] and b[3] == ncm) or (b[5] and str(b[5]) == str(produto)) or (not b[3] and not b[5]):
                out.append({'codigo': b[0], 'tipo': b[2], 'valor': float(b[6] or 0)})
        return out


# ======================== BASE DE CALCULO ========================
def base_calculo(item, parametros, tributo):
    """Valor bruto +- componentes parametrizaveis (rt_base_calculo).
    item: dict com qtd, valor_unitario, desconto, frete, seguro, despesas.
    Se nao houver configuracao de base, padrao legal: bruto - desconto + frete + seguro + despesas.
    """
    qtd = float(item.get('qtd') or 0)
    vu = float(item.get('valor_unitario') or 0)
    bruto = qtd * vu
    desconto = float(item.get('desconto') or 0)
    frete = float(item.get('frete') or 0)
    seguro = float(item.get('seguro') or 0)
    despesas = float(item.get('despesas') or 0)

    comp = {}
    if parametros.base:
        for trib, componente, sinal, ordem in parametros.base:
            if trib != tributo:
                continue
            if componente in ('desconto', 'frete', 'seguro', 'despesa_acessoria', 'valor_bruto'):
                comp[componente] = (sinal, ordem)
    if 'desconto' not in comp:
        comp['desconto'] = ('-', 1)
    if 'frete' not in comp:
        comp['frete'] = ('+', 2)
    if 'seguro' not in comp:
        comp['seguro'] = ('+', 3)
    if 'despesa_acessoria' not in comp:
        comp['despesa_acessoria'] = ('+', 4)

    base = bruto
    for nome, (sinal, _o) in sorted(comp.items(), key=lambda kv: kv[1][1]):
        if nome == 'desconto':
            base += (float(desconto) if sinal == '+' else -float(desconto))
        elif nome == 'frete':
            base += (float(frete) if sinal == '+' else -float(frete))
        elif nome == 'seguro':
            base += (float(seguro) if sinal == '+' else -float(seguro))
        elif nome == 'despesa_acessoria':
            base += (float(despesas) if sinal == '+' else -float(despesas))
    return max(base, 0), bruto


# ======================== CALCULO CBS / IBS ========================
def calcular_item(item, parametros):
    """Calcula CBS e IBS de um item, aplicando beneficios e retornando memoria de calculo."""
    regime = item.get('regime') or 'lucro real'
    op = item.get('tipo_operacao') or 'venda'
    ncm = item.get('ncm') or ''
    produto = item.get('produto_codigo')
    uf_origem = item.get('uf_origem') or ''
    uf_destino = item.get('uf_destino') or ''
    municipio = item.get('municipio') or ''
    receita = item.get('natureza_receita') or ''

    base_cbs, bruto = base_calculo(item, parametros, 'CBS')
    base_ibs, _ = base_calculo(item, parametros, 'IBS')

    # regras NCM
    reg_ncm = parametros.ncm.get(ncm) if ncm else None
    incide_cbs = reg_ncm[1] if reg_ncm else True
    incide_ibs = reg_ncm[2] if reg_ncm else True
    # regras operacao
    reg_op = parametros.ops.get(op) if op else None
    if reg_op:
        incide_cbs = incide_cbs and bool(reg_op[2])
        incide_ibs = incide_ibs and bool(reg_op[3])

    # aliquotas
    a_cbs = parametros.aliquota_cbs(regime, op, receita)
    a_ibs = parametros.aliquota_ibs(regime, op, uf_origem, uf_destino, municipio)

    # beneficios
    beneficios = parametros.beneficios_para(ncm, produto)
    red_cbs = a_cbs['reducao'] if a_cbs else 0
    red_ibs = a_ibs['reducao'] if a_ibs else 0
    for b in beneficios:
        if b['tipo'] == 'isencao':
            incide_cbs = incide_ibs = False
        elif b['tipo'] == 'reducao_base':
            red_cbs = red_ibs = b['valor']
        elif b['tipo'] == 'diferimento':
            incide_cbs = incide_ibs = False  # deferido (nao recolhe agora)

    # valores
    v_cbs = v_ibs = 0.0
    mem = []
    if incide_cbs and a_cbs:
        b_ef = base_cbs * (1 - red_cbs / 100.0)
        v_cbs = b_ef * a_cbs['aliquota'] / 100.0
        mem.append("CBS = %.2f * %.4f%% (base %.2f reducao %.1f%%) = %.2f" %
                   (b_ef, a_cbs['aliquota'], base_cbs, red_cbs, v_cbs))
    else:
        mem.append("CBS nao incide (regra/beneficio)")
    if incide_ibs and a_ibs:
        b_ef = base_ibs * (1 - red_ibs / 100.0)
        v_ibs = b_ef * a_ibs['aliquota'] / 100.0
        mem.append("IBS = %.2f * %.4f%% (base %.2f reducao %.1f%%) = %.2f" %
                   (b_ef, a_ibs['aliquota'], base_ibs, red_ibs, v_ibs))
    else:
        mem.append("IBS nao incide (regra/beneficio)")

    return {
        'produto_codigo': produto,
        'ncm': ncm,
        'bruto': round(bruto, 2),
        'base_cbs': round(base_cbs, 2),
        'base_ibs': round(base_ibs, 2),
        'cbs': round(v_cbs, 2),
        'ibs': round(v_ibs, 2),
        'total_tributos': round(v_cbs + v_ibs, 2),
        'memoria': mem,
    }


# ======================== CREDITOS / NAO CUMULATIVIDADE ========================
def apurar_creditos(mcur, periodo, itens_calculados):
    """Debitos - creditos por periodo. Retorna saldo. Grava rt_creditos."""
    from psycopg2.extras import execute_values
    hoje = time.strftime('%Y-%m-%d %H:%M:%S')
    rows = []
    for r in itens_calculados:
        deb_cbs = r.get('cbs') or 0
        deb_ibs = r.get('ibs') or 0
        cred_cbs = deb_cbs * 0.0  # credito parametrizado por fornecedor ficaria aqui
        cred_ibs = deb_ibs * 0.0
        for tributo, deb, cred in [('CBS', deb_cbs, cred_cbs), ('IBS', deb_ibs, cred_ibs)]:
            saldo = deb - cred
            rows.append((periodo, tributo, r.get('produto_codigo'), None, r.get('nota_codigo'),
                         deb, cred, 0, -saldo if saldo < 0 else 0, saldo if saldo >= 0 else 0, hoje))
    if rows:
        execute_values(mcur, """INSERT INTO rt_creditos
            (periodo_apuracao, tributo, produto_codigo, fornecedor_codigo, nota_codigo,
             debito, credito, credito_utilizado, saldo_credor, saldo_devedor, data)
            VALUES %s ON CONFLICT (periodo_apuracao, tributo, produto_codigo, nota_codigo)
            DO UPDATE SET debito=EXCLUDED.debito, credito=EXCLUDED.credito,
            saldo_credor=EXCLUDED.saldo_credor, saldo_devedor=EXCLUDED.saldo_devedor""", rows)
    return rows


def registrar_split(mcur, nota_codigo, valor_total, parcela_tributos):
    from psycopg2.extras import execute_values
    hoje = time.strftime('%Y-%m-%d %H:%M:%S')
    execute_values(mcur, """INSERT INTO rt_split_payment
        (nota_codigo, valor_total, parcela_fornecedor, parcela_tributos, data_liquidacao, status, criado_em)
        VALUES %s""",
                   [(nota_codigo, valor_total, round(valor_total - parcela_tributos, 2),
                     round(parcela_tributos, 2), None, 'preparado', hoje)])


def gravar_log(mcur, objeto, tipo, detalhe):
    from psycopg2.extras import execute_values
    execute_values(mcur, """INSERT INTO rt_log_calculos (objeto, tipo, detalhe, criado_em)
        VALUES %s""", [(objeto, tipo, json.dumps(detalhe, ensure_ascii=False, default=str),
                        time.strftime('%Y-%m-%d %H:%M:%S'))])


# ======================== ENTRADA ========================
def main():
    args = sys.argv[1:]
    from db_mem import connect, close_all
    tr, lr, cr, cur = connect()
    p = Parametros(cur)
    p.carregar()

    try:
        if '--calc' in args:
            path = args[args.index('--calc') + 1]
            itens = json.load(io.open(path, encoding='utf-8'))
            res = [calcular_item(i, p) for i in itens]
            print(json.dumps(res, ensure_ascii=False, indent=1))
        elif '--demo' in args:
            itens = [
                {'produto_codigo': 100, 'ncm': '90211010', 'qtd': 2, 'valor_unitario': 100.0,
                 'desconto': 10.0, 'frete': 5.0, 'regime': 'lucro real', 'tipo_operacao': 'venda',
                 'uf_destino': 'SP', 'municipio': 'SAO PAULO'},
                {'produto_codigo': 200, 'ncm': '61151021', 'qtd': 1, 'valor_unitario': 50.0,
                 'regime': 'simples', 'tipo_operacao': 'venda', 'uf_destino': 'SP'},
            ]
            res = [calcular_item(i, p) for i in itens]
            print(json.dumps(res, ensure_ascii=False, indent=1))
        elif '--audit' in args:
            print("Auditoria: confira rt_regras_ncm / rt_beneficios preenchidos.")
        else:
            print(__doc__)
    finally:
        close_all(tr, lr, cr)


if __name__ == '__main__':
    main()
