# -*- coding: utf-8 -*-
"""EMAIL DAS 9H - resumo diario de movimentacao + metricas aplicadas.
1) Tabelas que tiveram movimento hoje (Data_Alteracao >= hoje) com a
   quantidade de registros movimentados (ex.: Movimento 450, Estoque 285).
2) Abaixo, relatorio das metricas/aplicacoes feitas hoje (metricas_diario.json).
Uso: python email_9h.py            (manual)
     python email_9h.py --auto      (agendado 09:00, loga sem print)
"""
import os, sys, io, json, time, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

AUTO = '--auto' in sys.argv
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
CONFIG = BASE + r'\email_config.json'
LOGFILE = LOGDIR + r'\email_9h.log'
METRICAS_FILE = LOGDIR + r'\metricas_diario.json'

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

NOMES_AMIGAVES = {
    "Cli_For": "cadastro de clientes/fornecedores",
    "Prod_Serv": "cadastro de produtos/servicos",
    "Funcionarios": "cadastro de funcionarios",
    "Configuracoes": "configuracoes",
    "Configuracoes_Filtros": "filtros de configuracao",
    "Prod_Serv_Promocao": "promocoes de produtos",
    "Carga_Tributaria_Estados": "carga tributaria por estado",
    "Estoque_Atual": "estoque",
    "Estoque_Atual_Lote": "estoque por lote",
    "Financeiro_Caixa": "caixa",
    "Financeiro_Contas": "contas financeiras",
    "Movimento_Prod_Serv": "movimento de produtos/servicos",
    "Movimento": "movimento",
    "Movimento_Entrega": "entregas",
    "Movimento_Documentos_Fiscais": "documentos fiscais",
    "Movimento_Estoque_Efetivado": "estoque efetivado",
    "Financeiro_Formas_Pagamento_V2": "formas de pagamento",
    "Contas_V2": "contas",
    "Funcionarios_Conf": "configuracoes de funcionarios",
    "concorrente": "precos concorrentes",
    "Movimento_Origem": "movimento origem",
    "Movimento_Prod_Serv_Lote": "movimento por lote",
}


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOGFILE, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    if not AUTO:
        print(line)


def load_config():
    if os.path.exists(CONFIG):
        return json.load(io.open(CONFIG, encoding='utf-8'))
    return {
        "smtp_host": "mail.consultoriasoft.com.br",
        "smtp_port": 26,
        "remetente": os.environ.get("EMAIL_REMETENTE", ""),
        "senha_app": os.environ.get("EMAIL_SENHA_APP", ""),
        "destinatario": os.environ.get("EMAIL_DESTINATARIO", ""),
    }


def nome_amigavel(t):
    for k, v in NOMES_AMIGAVES.items():
        if t == k or t.startswith(k):
            return v
    return "tabela " + t


def coletar_movimentacao_hoje():
    """Para cada tabela com Data_Alteracao: quantos registros mudaram hoje."""
    import pyodbc
    sconn = pyodbc.connect(sync.SQL_DSN, timeout=60)
    scur = sconn.cursor()
    hoje0 = time.strftime('%Y-%m-%d') + ' 00:00:00'
    res = []
    for t in sync.tabelas:
        try:
            r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
            if r[0] == 0:
                continue
            scur.execute("""SELECT COUNT(*) FROM dbo.[%s]
                WHERE Data_Alteracao >= ?""" % t, (hoje0,))
            qtd = scur.fetchone()[0]
            if qtd > 0:
                res.append((nome_amigavel(t), int(qtd)))
        except Exception:
            continue
    sconn.close()
    res.sort(key=lambda x: -x[1])
    return res


def ler_metricas_hoje():
    hoje = time.strftime('%Y-%m-%d')
    try:
        d = json.load(io.open(METRICAS_FILE, encoding='utf-8'))
        items = d.get(hoje, [])
        return items if isinstance(items, list) else []
    except Exception:
        return []


def relatorio_do_dia():
    """Caminho do relatorio completo do analista de hoje (se existir)."""
    for dia in (time.strftime('%Y-%m-%d'),):
        p = os.path.join(LOGDIR, 'relatorio_analista_%s.md' % dia)
        if os.path.exists(p):
            return p
    return None


def ler_json(path):
    try:
        return json.load(io.open(path, encoding='utf-8'))
    except Exception:
        return None


def ultima_linha_log(path):
    try:
        with io.open(path, encoding='utf-8', errors='ignore') as f:
            linhas = f.read().strip().splitlines()
        return linhas[-1] if linhas else ''
    except Exception:
        return ''


def metricas_automaticas():
    """Metricas geradas automaticamente a partir do estado real dos servicos
    (heartbeats, checkpoints, logs). Nao depende de registro manual."""
    hoje = time.strftime('%Y-%m-%d')
    hoje0 = hoje + ' 00:00:00'
    met = []

    # --- memoria ---
    hb = ler_json(r'C:\S9\logs\heartbeat_memoria.json') or {}
    ciclo = hb.get('ciclo', 0)
    if ciclo:
        met.append("Memoria: ciclo %s executado (%s)" % (ciclo, hb.get('horario', '')))

    # --- sync ---
    hbs = ler_json(r'C:\S9\logs\heartbeat_sync.json') or {}
    if hbs.get('ciclo_concluido'):
        met.append("Sync: ultimo ciclo concluido as %s" % hbs.get('ciclo_concluido'))

    # --- coletor ---
    hbc = ler_json(r'C:\S9\logs\heartbeat_coletor.json') or {}
    if hbc.get('status') == 'processando':
        met.append("Coletor: %s produtos processados no ciclo (ultimo: %s)" %
                   (hbc.get('feitos', 0), hbc.get('produto_atual', '')))
    elif hbc.get('status'):
        met.append("Coletor: %s" % hbc.get('status'))

    # --- fotos / mega ---
    m = ultima_linha_log(r'C:\S9\logs\mega_%s.log' % time.strftime('%Y%m%d'))
    if not m:
        import glob
        logs = sorted(glob.glob(r'C:\S9\logs\mega_*.log'), key=lambda p: -os.path.getmtime(p))
        if logs:
            m = ultima_linha_log(logs[0])
    if m:
        met.append("MEGA/fotos: %s" % m.strip())

    # --- backup ---
    b = ultima_linha_log(r'C:\S9\logs\backup.log')
    if b:
        met.append("Backup MEGA: %s" % b.strip())

    # --- autopush ---
    a = ultima_linha_log(r'C:\S9\logs\autopush.log')
    if a:
        met.append("GitHub: %s" % a.strip())

    # --- API memoria ---
    ap = ultima_linha_log(r'C:\S9\logs\memoria_api.log')
    if ap:
        met.append("API memoria: %s" % ap.strip())

    return met


def montar_html(mov, metricas, hoje):
    sec_mov = ""
    if mov:
        rows = "".join("<tr><td>%s</td><td align='center'><b>%d</b></td></tr>" % (t, q) for t, q in mov[:40])
        total = sum(q for _, q in mov)
        rows += "<tr style='background:#e0f2fe;font-weight:bold'><td>TOTAL</td><td align='center'>%d</td></tr>" % total
        sec_mov = """<h3 style="color:#0ea5e9;margin:0">Tabelas com movimentacao hoje</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:14px">
          <tr style="background:#f1f5f9"><th>Tabela</th><th>Registros</th></tr>
          %s
        </table>""" % rows
    else:
        sec_mov = "<h3 style='color:#0ea5e9'>Tabelas com movimentacao hoje</h3><p>Nenhum registro movimentado hoje.</p>"

    sec_met = ""
    if metricas:
        items = "".join("<li>%s</li>" % m for m in metricas)
        sec_met = """<h3 style="color:#0ea5e9;margin:0">Metricas aplicadas hoje</h3>
        <ul style="font-size:14px">%s</ul>""" % items
    else:
        sec_met = "<h3 style='color:#0ea5e9'>Metricas aplicadas hoje</h3><p>Nenhuma metrica registrada.</p>"

    return """<html><body style="font-family:Segoe UI,Arial;color:#1e293b">
    <h2 style="color:#0ea5e9">Resumo diario S9 - %s</h2>
    %s
    <br><br>
    %s
    <p style="color:#64748b;font-size:12px">Enviado automaticamente as 09:00.</p>
    </body></html>""" % (hoje, sec_mov, sec_met)


def montar_texto(mov, metricas, hoje):
    txt = ["Resumo diario S9 - %s" % hoje, ""]
    txt.append("TABELAS COM MOVIMENTACAO HOJE:")
    if mov:
        for t, q in mov[:40]:
            txt.append("  %s %d" % (t, q))
        txt.append("  TOTAL %d" % sum(q for _, q in mov))
    else:
        txt.append("  nenhum registro movimentado hoje")
    txt.append("")
    txt.append("METRICAS APLICADAS HOJE:")
    if metricas:
        for m in metricas:
            txt.append("  - %s" % m)
    else:
        txt.append("  nenhuma metrica registrada")
    return "\n".join(txt)


def enviar(cfg, html, texto, hoje, anexo=None):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr(('Resumo S9', cfg['remetente']))
    msg['To'] = cfg['destinatario']
    msg['Subject'] = 'Resumo S9 - %s' % hoje
    msg.attach(MIMEText(texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    if anexo and os.path.exists(anexo):
        from email.mime.base import MIMEBase
        from email import encoders
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(io.open(anexo, 'rb').read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(anexo))
        msg.attach(part)
    s = smtplib.SMTP(cfg['smtp_host'], int(cfg['smtp_port']), timeout=60)
    s.starttls()
    s.login(cfg['remetente'], cfg['senha_app'])
    s.sendmail(cfg['remetente'], [cfg['destinatario']], msg.as_string())
    s.quit()


def main():
    hoje = time.strftime('%Y-%m-%d')
    cfg = load_config()
    if not cfg.get('remetente') or not cfg.get('senha_app'):
        log("Configuracao de email incompleta")
        return 1
    mov = coletar_movimentacao_hoje()
    metricas_auto = metricas_automaticas()
    metricas_manual = ler_metricas_hoje()
    metricas = metricas_auto + metricas_manual
    if not metricas:
        metricas = ["Nenhuma metrica registrada no dia."]
    html = montar_html(mov, metricas, hoje)
    texto = montar_texto(mov, metricas, hoje)
    anexo = relatorio_do_dia()
    enviar(cfg, html, texto, hoje, anexo)
    log("Email 9h enviado - tabelas=%d metricas=%d (auto=%d manual=%d) anexo=%s" %
        (len(mov), len(metricas), len(metricas_auto), len(metricas_manual), os.path.basename(anexo) if anexo else 'nenhum'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
