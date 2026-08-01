# -*- coding: utf-8 -*-
"""RT_SETUP - cria o schema de parametrizacao da Reforma Tributaria (CBS/IBS)
no banco 'postgres' da VPS. Todas as aliquotas/regras ficam EM BANCO (nunca no codigo).

Idempotente: CREATE TABLE IF NOT EXISTS + ALTER ADD COLUMN para evolucao.

Tabelas criadas:
  rt_aliquotas_cbs       - aliquotas da CBS por vigencia/regime/operacao/receita
  rt_aliquotas_ibs       - aliquotas do IBS por vigencia/regime/operacao/uf
  rt_base_calculo        - componentes da base de calculo (parametrizavel)
  rt_beneficios          - beneficios fiscais por NCM/CEST/produto/vigencia
  rt_regras_ncm          - regras por NCM (incidencia, credito, reducao)
  rt_regras_operacao     - regras por tipo de operacao
  rt_regras_uf           - regras por UF (origem/destino)
  rt_regras_municipio    - regras por municipio
  rt_regras_regime       - regras por regime tributario do contribuinte
  rt_creditos            - controle de creditos (nao cumulatividade)
  rt_split_payment       - pagamento segregado
  rt_log_calculos        - memoria de calculo auditavel
  rt_parametros          - parametros gerais (vigencia de transicao, etc.)

Uso: python rt_setup.py
"""
import sys, io

sys.path.insert(0, r'C:\S9\knowledge_base')
from db_mem import connect, close_all

TABELAS = {
    "rt_parametros": """(
        id SERIAL PRIMARY KEY,
        chave TEXT UNIQUE NOT NULL,
        valor TEXT,
        descricao TEXT,
        vigencia_inicio DATE,
        vigencia_fim DATE,
        atualizado_em TIMESTAMP)""",
    "rt_aliquotas_cbs": """(
        id SERIAL PRIMARY KEY,
        vigencia_inicio DATE NOT NULL,
        vigencia_fim DATE,
        regime TEXT,            -- simples, lucro real, lucro presumido
        tipo_operacao TEXT,     -- venda, compra, transferencia, etc.
        natureza_receita TEXT,  -- NBS / natureza da receita
        aliquota NUMERIC(7,4) NOT NULL,
        reduzir_base NUMERIC(7,4) DEFAULT 0,
        credito_percentual NUMERIC(7,4) DEFAULT 0,
        descricao TEXT,
        UNIQUE (vigencia_inicio, regime, tipo_operacao, natureza_receita))""",
    "rt_aliquotas_ibs": """(
        id SERIAL PRIMARY KEY,
        vigencia_inicio DATE NOT NULL,
        vigencia_fim DATE,
        regime TEXT,
        tipo_operacao TEXT,
        uf_origem TEXT,
        uf_destino TEXT,
        municipio TEXT,
        aliquota NUMERIC(7,4) NOT NULL,
        reduzir_base NUMERIC(7,4) DEFAULT 0,
        credito_percentual NUMERIC(7,4) DEFAULT 0,
        descricao TEXT,
        UNIQUE (vigencia_inicio, regime, tipo_operacao, uf_origem, uf_destino, municipio))""",
    "rt_base_calculo": """(
        id SERIAL PRIMARY KEY,
        tributo TEXT NOT NULL,          -- CBS, IBS
        componente TEXT NOT NULL,       -- desconto, frete, seguro, despesa_acessoria, valor_bruto
        sinal TEXT DEFAULT '-',         -- + ou -
        ordem INT DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE,
        UNIQUE (tributo, componente, vigencia_inicio))""",
    "rt_beneficios": """(
        id SERIAL PRIMARY KEY,
        codigo TEXT,
        descricao TEXT,
        tipo TEXT,                      -- isencao, reducao_base, credito_presumido, diferimento
        ncm TEXT,
        cest TEXT,
        produto_codigo TEXT,
        valor NUMERIC(7,4),
        vigencia_inicio DATE NOT NULL,
        vigencia_fim DATE,
        ativo BOOLEAN DEFAULT TRUE)""",
    "rt_regras_ncm": """(
        id SERIAL PRIMARY KEY,
        ncm TEXT,
        incide_cbs BOOLEAN DEFAULT TRUE,
        incide_ibs BOOLEAN DEFAULT TRUE,
        credito_cbs_percentual NUMERIC(7,4) DEFAULT 0,
        credito_ibs_percentual NUMERIC(7,4) DEFAULT 0,
        nbs TEXT,
        reducao_base_percentual NUMERIC(7,4) DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE)""",
    "rt_regras_operacao": """(
        id SERIAL PRIMARY KEY,
        tipo_operacao TEXT,
        finalidade TEXT,
        incide_cbs BOOLEAN DEFAULT TRUE,
        incide_ibs BOOLEAN DEFAULT TRUE,
        credito_cbs_percentual NUMERIC(7,4) DEFAULT 0,
        credito_ibs_percentual NUMERIC(7,4) DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE)""",
    "rt_regras_uf": """(
        id SERIAL PRIMARY KEY,
        uf TEXT,
        aliquota_ibs_estadual NUMERIC(7,4),
        credito_percentual NUMERIC(7,4) DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE)""",
    "rt_regras_municipio": """(
        id SERIAL PRIMARY KEY,
        municipio TEXT,
        uf TEXT,
        aliquota_ibs_municipal NUMERIC(7,4),
        credito_percentual NUMERIC(7,4) DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE)""",
    "rt_regras_regime": """(
        id SERIAL PRIMARY KEY,
        regime TEXT,
        tipo_contribuinte TEXT,         -- contribuinte, nao contribuinte, consumidor final
        credito_cbs_percentual NUMERIC(7,4) DEFAULT 0,
        credito_ibs_percentual NUMERIC(7,4) DEFAULT 0,
        vigencia_inicio DATE,
        vigencia_fim DATE)""",
    "rt_creditos": """(
        id SERIAL PRIMARY KEY,
        periodo_apuracao TEXT,
        tributo TEXT,
        produto_codigo TEXT,
        fornecedor_codigo TEXT,
        nota_codigo TEXT,
        debito NUMERIC(16,2) DEFAULT 0,
        credito NUMERIC(16,2) DEFAULT 0,
        credito_utilizado NUMERIC(16,2) DEFAULT 0,
        saldo_credor NUMERIC(16,2) DEFAULT 0,
        saldo_devedor NUMERIC(16,2) DEFAULT 0,
        data TIMESTAMP,
        UNIQUE (periodo_apuracao, tributo, produto_codigo, nota_codigo))""",
    "rt_split_payment": """(
        id SERIAL PRIMARY KEY,
        nota_codigo TEXT,
        valor_total NUMERIC(16,2),
        parcela_fornecedor NUMERIC(16,2),
        parcela_tributos NUMERIC(16,2),
        data_liquidacao TIMESTAMP,
        status TEXT,
        criado_em TIMESTAMP)""",
    "rt_log_calculos": """(
        id SERIAL PRIMARY KEY,
        objeto TEXT,
        tipo TEXT,
        detalhe JSONB,
        criado_em TIMESTAMP)""",
}

# colunas adicionadas em versoes futuras
ADDCOLS = []


def main():
    tr, lr, cr, cur = connect()
    try:
        for nome, ddl in TABELAS.items():
            cur.execute("CREATE TABLE IF NOT EXISTS %s %s" % (nome, ddl))
        for tab, col, ctype in ADDCOLS:
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s", (tab, col))
            if not cur.fetchone():
                cur.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (tab, col, ctype))
        cr.commit()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'rt\\_%%' ORDER BY table_name")
        tabs = [r[0] for r in cur.fetchall()]
        print("Schema rt_* pronto (%d tabelas):" % len(tabs))
        for t in tabs:
            print("  -", t)
    finally:
        close_all(tr, lr, cr)


if __name__ == '__main__':
    main()
