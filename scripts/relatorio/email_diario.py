# -*- coding: utf-8 -*-
"""RELATORIO DIARIO POR E-MAIL - tabelas com modificacoes (antes/depois).
Gera um log das tabelas que tiveram modificacoes comparando SQL Server
(origem) x PostgreSQL (destino) via Data_Alteracao e envia por e-mail.

Configuracao em email_config.json (ou variaveis de ambiente):
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "remetente": "seuemail@gmail.com",
  "senha_app": "aaaaaaaaaaaaaaaa",
  "destinatario": "contato@consultoriasoft.com.br"
}
"""
import io, json, os, sys, smtplib, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
CONFIG = BASE + r'\email_config.json'

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

SQL_DSN = sync.SQL_DSN

def load_config():
    if os.path.exists(CONFIG):
        return json.load(io.open(CONFIG, encoding='utf-8'))
    return {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "remetente": os.environ.get("EMAIL_REMETENTE", ""),
        "senha_app": os.environ.get("EMAIL_SENHA_APP", ""),
        "destinatario": "contato@consultoriasoft.com.br",
    }

def coletar_layout():
    """Retorna lista de alteracoes de layout (colunas novas / tipo diferente) SQL vs PG."""
    import pyodbc, psycopg2
    sconn = pyodbc.connect(SQL_DSN, timeout=30)
    scur = sconn.cursor()
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    pcur = pconn.cursor()
    layout = []
    for t in sync.tabelas:
        try:
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
                pgtype = sync.conv_type(dt, length, prec, scale)
                key = name.lower()
                if key not in pg:
                    short = name[:63].lower()
                    if short in pg:
                        continue
                    layout.append((t, 'ADD', name, '-', pgtype))
                else:
                    atual = sync.pg_type_of_row(pg[key])
                    if sync.norm_type(atual) != sync.norm_type(pgtype):
                        layout.append((t, 'ALTER', name, atual, pgtype))
        except Exception:
            continue
    pconn.close(); sconn.close(); tunnel.close(); local.close()
    return layout

def coletar_alteracoes():
    """Retorna lista de tabelas com Data_Alteracao mais recente que o PG."""
    import pyodbc, psycopg2
    sconn = pyodbc.connect(SQL_DSN, timeout=30)
    scur = sconn.cursor()
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    pcur = pconn.cursor()
    linhas = []
    for t in sync.tabelas:
        r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
        if r[0] == 0:
            continue
        pk = scur.execute("""SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE c
            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc ON c.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
            WHERE c.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='PRIMARY KEY'""", t).fetchall()
        pkcol = pk[0][0] if pk else None
        if not pkcol:
            continue
        try:
            pcur.execute('SELECT max("Data_Alteracao") FROM "%s"' % t)
            pg_max = pcur.fetchone()[0]
            scur.execute("""SELECT COUNT(*) FROM dbo.[%s]
                WHERE Data_Alteracao > ?""" % t, (pg_max,))
            qtd = scur.fetchone()[0]
            if qtd > 0:
                linhas.append((t, qtd, str(pg_max)))
        except Exception:
            continue
    pconn.close(); sconn.close(); tunnel.close(); local.close()
    linhas.sort(key=lambda x: -x[1])
    return linhas

NOMES_AMIGAVES = {
    "Cli_For": "cadastro de clientes/fornecedores",
    "Prod_Serv": "cadastro de produtos/servicos",
    "Funcionarios": "cadastro de funcionarios",
    "Configuracoes": "configuracoes",
    "Configuracoes_Filtros": "filtros de configuracao",
    "Prod_Serv_Promocao": "promocoes de produtos",
    "Carga_Tributaria_Estados": "carga tributaria por estado",
    "Estoque_Atual": "estoque atual",
    "Estoque_Atual_Lote": "estoque por lote",
    "Financeiro_Caixa": "movimento de caixa",
    "Financeiro_Contas": "contas financeiras",
    "Movimento_Prod_Serv": "movimento de produtos/servicos",
    "Movimento": "movimentos",
    "Financeiro_Formas_Pagamento_V2": "formas de pagamento",
    "Contas_V2": "contas",
    "Funcionarios_Conf": "configuracoes de funcionarios",
}

def nome_amigavel(t):
    for k, v in NOMES_AMIGAVES.items():
        if t == k or t.startswith(k):
            return v
    return "tabela " + t

def montar_html(linhas, layout, hoje):
    sec_dados = ""
    if linhas:
        rows = ""
        for t, qtd, pg_max in linhas:
            data = (pg_max or "")[:10]
            rows += ("<tr><td>%s</td><td align='center'>%d itens</td>"
                     "<td align='center'>%s</td></tr>") % (nome_amigavel(t), qtd, data)
        sec_dados = """
        <h3 style="color:#0ea5e9;margin-top:0">Dados alterados</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:13px">
          <tr style="background:#f1f5f9">
            <th>Cadastro</th><th>Alterado</th><th>Data</th>
          </tr>
          %s
        </table>""" % rows
    else:
        sec_dados = "<h3 style='color:#0ea5e9'>Dados alterados</h3><p>Nenhum cadastro alterado.</p>"

    sec_layout = ""
    if layout:
        rows = ""
        for t, acao, coluna, antes, depois in layout:
            rows += ("<tr><td>%s</td><td align='center'>%s</td><td class='mono'>%s</td>"
                     "<td class='mono'>%s</td><td class='mono'>%s</td></tr>") % (
                nome_amigavel(t), acao, coluna, antes, depois)
        sec_layout = """
        <h3 style="color:#f59e0b;margin-top:0">Layout alterado</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:13px">
          <tr style="background:#f1f5f9">
            <th>Cadastro</th><th>Alteracao</th><th>Coluna</th><th>Antes</th><th>Depois</th>
          </tr>
          %s
        </table>""" % rows
    else:
        sec_layout = "<h3 style='color:#f59e0b'>Layout alterado</h3><p>Nenhuma alteracao de layout.</p>"

    html = """
    <html><body style="font-family:Segoe UI,Arial;color:#1e293b">
    <h2 style="color:#0ea5e9">Resumo diario - Sincronizador S9</h2>
    <p>Data: <b>%s</b></p>
    <table width="100%%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="45%%" valign="top">%s</td>
        <td width="10%%">&nbsp;</td>
        <td width="45%%" valign="top">%s</td>
      </tr>
    </table>
    <p style="color:#64748b;font-size:12px">Enviado automaticamente pelo sincronizador silencioso.</p>
    </body></html>
    """ % (hoje, sec_dados, sec_layout)
    return html

def montar_texto(linhas, layout, hoje):
    txt = ["Resumo diario - Sincronizador S9", "Data: %s" % hoje, ""]
    if linhas:
        txt.append("DADOS ALTERADOS:")
        for t, qtd, pg_max in linhas:
            data = (pg_max or "")[:10]
            txt.append("  %s - %d itens - data %s" % (nome_amigavel(t), qtd, data))
    else:
        txt.append("DADOS ALTERADOS: nenhum cadastro alterado.")
    txt.append("")
    if layout:
        txt.append("LAYOUT ALTERADO:")
        for t, acao, coluna, antes, depois in layout:
            txt.append("  %s [%s] coluna %s %s -> %s" % (nome_amigavel(t), acao, coluna, antes, depois))
    else:
        txt.append("LAYOUT ALTERADO: nenhuma alteracao de layout.")
    return "\n".join(txt)

def enviar(cfg, html, texto, hoje):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr(('Sincronizador S9', cfg['remetente']))
    msg['To'] = cfg['destinatario']
    msg['Subject'] = 'Resumo diario Sincronizador - %s' % hoje
    msg.attach(MIMEText(texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    s = smtplib.SMTP(cfg['smtp_host'], int(cfg['smtp_port']), timeout=60)
    s.starttls()
    s.login(cfg['remetente'], cfg['senha_app'])
    s.sendmail(cfg['remetente'], [cfg['destinatario']], msg.as_string())
    s.quit()

def main():
    hoje = time.strftime('%Y-%m-%d')
    cfg = load_config()
    if not cfg['remetente'] or not cfg['senha_app']:
        raise SystemExit("Configuracao de email incompleta: preencha email_config.json")
    linhas = coletar_alteracoes()
    layout = coletar_layout()
    os.makedirs(LOGDIR, exist_ok=True)
    with io.open(LOGDIR + r'\email_%s.log' % hoje, 'a', encoding='utf-8') as f:
        f.write("[%s] %d tabelas alteradas, %d alteracoes de layout\n" % (time.strftime('%H:%M:%S'), len(linhas), len(layout)))
        for t, qtd, pg_max in linhas:
            f.write("  DADOS %s alterados=%d ultimo_pg=%s\n" % (t, qtd, pg_max))
        for t, acao, coluna, antes, depois in layout:
            f.write("  LAYOUT %s %s %s %s->%s\n" % (t, acao, coluna, antes, depois))
    if not linhas and not layout:
        print("Sem alteracoes - email NAO enviado")
        return
    html = montar_html(linhas, layout, hoje)
    texto = montar_texto(linhas, layout, hoje)
    enviar(cfg, html, texto, hoje)
    print("Email enviado para %s - %d tabelas alteradas, %d layout" % (cfg['destinatario'], len(linhas), len(layout)))

if __name__ == '__main__':
    main()
