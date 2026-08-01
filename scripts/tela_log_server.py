# -*- coding: utf-8 -*-
"""TELA DE LOG DIARIO - dashboard web local.
Servidor HTTP na porta 8090 que mostra o log completo diario do que foi salvo
pelo sincronizador, com atualizacao automatica e exportacao JSON.

- Abrir no navegador: http://localhost:8090
- JSON do dia: http://localhost:8090/json?dia=AAAA-MM-DD
"""
import io, json, os, glob
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
PORT = 8090

def listar_dias():
    dias = []
    for f in glob.glob(LOGDIR + r'\sync_*.json'):
        d = os.path.basename(f).replace('sync_', '').replace('.json', '')
        if len(d) == 10:
            dias.append(d)
    return sorted(dias, reverse=True)

def ler_dia(dia):
    path = LOGDIR + r'\sync_%s.json' % dia
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out

def resumo(registros):
    tot_salvo = sum(r.get('registros', 0) for r in registros if r.get('tipo') == 'salvo')
    n_salvo = sum(1 for r in registros if r.get('tipo') == 'salvo')
    n_erro = sum(1 for r in registros if r.get('tipo') == 'erro')
    n_est = sum(1 for r in registros if r.get('tipo') == 'estrutura')
    return tot_salvo, n_salvo, n_erro, n_est

# ---------- CONCORRENTE (acesso ao PG) ----------
def conectar_pg():
    import sys, importlib.util
    sys.path.insert(0, BASE)
    _spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
    sync = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sync)
    import psycopg2
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    return pconn, tunnel, local

def concorrente_dia(dia):
    """Retorna precos concorrentes de um dia (com preco da loja)."""
    try:
        pconn, tunnel, local = conectar_pg()
        cur = pconn.cursor()
        cur.execute("""
            SELECT c.id, c.produto_codigo, c.produto_nome, c.ean, c.ean3, c.concorrente,
                   c.url, c.preco, c.preco_avista, c.preco_pix, c.site_empresa,
                   c.cidade, c.estado, c.data_preco, c.data_coleta, c.foto_local, c.foto_mega
            FROM concorrente c
            WHERE c.data_preco = %s
            ORDER BY c.produto_codigo, c.preco
        """, (dia,))
        rows = cur.fetchall()
        pconn.close(); tunnel.close(); local.close()
        out = []
        for r in rows:
            out.append({
                'id': r[0], 'produto_codigo': r[1], 'produto_nome': r[2],
                'ean': r[3], 'ean3': r[4], 'concorrente': r[5] or '', 'url': r[6] or '',
                'preco': float(r[7]) if r[7] else None,
                'preco_avista': float(r[8]) if r[8] else None,
                'preco_pix': float(r[9]) if r[9] else None,
                'site_empresa': r[10] or '', 'cidade': r[11] or '', 'estado': r[12] or '',
                'data_preco': str(r[13]), 'data_coleta': str(r[14]),
                'foto_local': r[15] or '', 'foto_mega': r[16] or '',
            })
        return out
    except Exception as e:
        return {'erro': str(e)[:200]}

def concorrente_produtos():
    """Lista produtos que tem registro na concorrente (para busca)."""
    try:
        pconn, tunnel, local = conectar_pg()
        cur = pconn.cursor()
        cur.execute("""SELECT DISTINCT produto_codigo, produto_nome, ean
            FROM concorrente ORDER BY produto_codigo""")
        rows = cur.fetchall()
        pconn.close(); tunnel.close(); local.close()
        return [{'produto_codigo': r[0], 'produto_nome': r[1], 'ean': r[2]} for r in rows]
    except Exception as e:
        return {'erro': str(e)[:200]}

def concorrente_resumo():
    """Resumo geral: total precos, produtos monitorados, menor/maior preco."""
    try:
        pconn, tunnel, local = conectar_pg()
        cur = pconn.cursor()
        cur.execute("""SELECT COUNT(*), COUNT(DISTINCT produto_codigo),
            MIN(data_preco), MAX(data_preco) FROM concorrente""")
        r = cur.fetchone()
        pconn.close(); tunnel.close(); local.close()
        return {'total': r[0], 'produtos': r[1], 'min_data': str(r[2]), 'max_data': str(r[3])}
    except Exception as e:
        return {'erro': str(e)[:200]}

PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Tela de Log Diario - Sincronizador S9</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
  h1 { font-size: 22px; margin-bottom: 4px; color: #38bdf8; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 20px; }
  .bar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
  select, button { background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
    padding: 8px 12px; border-radius: 6px; font-size: 14px; }
  button { cursor: pointer; background: #0ea5e9; color: #083344; font-weight: 600; border: none; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px; }
  .card .num { font-size: 26px; font-weight: 700; }
  .card .lbl { font-size: 12px; color: #94a3b8; margin-top: 2px; }
  .salvo { color: #4ade80; } .erro { color: #f87171; } .est { color: #facc15; }
  table { width: 100%; border-collapse: collapse; background: #1e293b;
    border: 1px solid #334155; border-radius: 10px; overflow: hidden; }
  th { background: #334155; text-align: left; padding: 10px 12px; font-size: 13px; }
  td { padding: 8px 12px; font-size: 13px; border-top: 1px solid #1e293b; }
  tr:hover td { background: #243349; }
  .tag { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .tag.salvo { background: rgba(74,222,128,.15); color: #4ade80; }
  .tag.erro { background: rgba(248,113,113,.15); color: #f87171; }
  .tag.estrutura { background: rgba(250,204,21,.15); color: #facc15; }
  .tag.ciclo { background: rgba(148,163,184,.15); color: #94a3b8; }
  .tag.inicio { background: rgba(56,189,248,.15); color: #38bdf8; }
  .mono { font-family: Consolas, monospace; font-size: 12px; }
  pre { background: #0b1220; border: 1px solid #334155; border-radius: 8px;
    padding: 12px; margin-top: 16px; max-height: 400px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
  #atualiza { color: #4ade80; font-size: 12px; margin-left: auto; }
  .tabs { display: flex; gap: 8px; margin-bottom: 20px; }
  .tab { cursor: pointer; background: #1e293b; color: #94a3b8; border: 1px solid #334155;
    padding: 8px 18px; border-radius: 8px; font-size: 14px; font-weight: 600; }
  .tab.ativa { background: #0ea5e9; color: #083344; border-color: #0ea5e9; }
  input#cod_c { width: 180px; }
</style>
</head>
<body>
  <h1>Tela de Log Diario - Sincronizador</h1>
  <div class="sub">Log diario do sincronizador SQL Server &rarr; PostgreSQL &middot; atualizacao automatica a cada 5 min</div>

  <div class="tabs">
    <button class="tab ativa" onclick="aba('log')">Log Diario</button>
    <button class="tab" onclick="aba('concorrente')">Concorrente</button>
  </div>

  <div id="aba_log">
  <div class="bar">
    <label>Dia:</label>
    <select id="dia"></select>
    <button onclick="recarregar()">Atualizar agora</button>
    <button onclick="mostrarJson()">Ver JSON</button>
    <button onclick="copiarJson()">Copiar JSON</button>
    <span id="atualiza">&#9679; auto</span>
  </div>

  <div class="cards">
    <div class="card"><div class="num salvo" id="c1">0</div><div class="lbl">Registros salvos</div></div>
    <div class="card"><div class="num" id="c2">0</div><div class="lbl">Tabelas com salvamento</div></div>
    <div class="card"><div class="num est" id="c3">0</div><div class="lbl">Alteracoes de estrutura</div></div>
    <div class="card"><div class="num erro" id="c4">0</div><div class="lbl">Erros</div></div>
    <div class="card"><div class="num" id="c5">0</div><div class="lbl">Total de eventos</div></div>
  </div>

  <table id="tbl">
    <thead><tr><th>Hora</th><th>Tipo</th><th>Tabela</th><th>Registros</th><th>Detalhe</th></tr></thead>
    <tbody></tbody>
  </table>

  <pre id="json" style="display:none"></pre>
  </div>

  <div id="aba_concorrente" style="display:none">
  <div class="bar">
    <label>Dia:</label>
    <select id="dia_c"></select>
    <label>Produto:</label>
    <input id="cod_c" placeholder="Codigo do produto" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;padding:8px 12px;border-radius:6px;font-size:14px">
    <button onclick="carregarConcorrente()">Carregar</button>
    <button onclick="copiarConcJson()">Copiar JSON</button>
    <span id="atualiza_c">&#9679; auto</span>
  </div>

  <div class="cards">
    <div class="card"><div class="num salvo" id="cc1">0</div><div class="lbl">Precos coletados</div></div>
    <div class="card"><div class="num" id="cc2">0</div><div class="lbl">Produtos monitorados</div></div>
    <div class="card"><div class="num" id="cc3">-</div><div class="lbl">Data inicial</div></div>
    <div class="card"><div class="num" id="cc4">-</div><div class="lbl">Data final</div></div>
  </div>

  <table id="tbl_c">
    <thead><tr><th>Foto</th><th>Codigo</th><th>Produto</th><th>EAN</th><th>EAN3</th><th>Empresa</th><th>Preco</th><th>A vista</th><th>Pix</th><th>Site</th><th>Cidade</th><th>Data</th></tr></thead>
    <tbody></tbody>
  </table>
  <pre id="json_c" style="display:none"></pre>
  </div>

<script>
var dias = [];
var atual = null;
var abaAtiva = 'log';

function aba(n){
  abaAtiva = n;
  document.getElementById('aba_log').style.display = n == 'log' ? '' : 'none';
  document.getElementById('aba_concorrente').style.display = n == 'concorrente' ? '' : 'none';
  document.querySelectorAll('.tab').forEach(function(b){
    b.classList.toggle('ativa', b.getAttribute('onclick').indexOf(n) >= 0);
  });
  if (n == 'concorrente') carregarConcorrente();
  else recarregar();
}

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function brl(v){ return 'R$ ' + Number(v||0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2}); }

async function loadDias(){
  var r = await fetch('/dias');
  dias = await r.json();
  ['dia','dia_c'].forEach(function(id){
    var sel = document.getElementById(id);
    sel.innerHTML = '';
    dias.forEach(function(d){
      var o = document.createElement('option');
      o.value = d; o.textContent = d;
      sel.appendChild(o);
    });
  });
  var hoje = new Date().toISOString().slice(0,10);
  if (dias.indexOf(hoje) >= 0){ document.getElementById('dia').value = hoje; document.getElementById('dia_c').value = hoje; }
  else if (dias.length){ document.getElementById('dia').value = dias[0]; document.getElementById('dia_c').value = dias[0]; }
  atual = document.getElementById('dia').value;
}

async function recarregar(){
  var sel = document.getElementById('dia');
  atual = sel.value;
  var r = await fetch('/json?dia=' + atual);
  var regs = await r.json();
  render(regs);
}

function render(regs){
  var tot_salvo = 0, n_salvo = 0, n_erro = 0, n_est = 0;
  regs.forEach(function(x){
    if (x.tipo == 'salvo'){ tot_salvo += (x.registros||0); n_salvo++; }
    else if (x.tipo == 'erro') n_erro++;
    else if (x.tipo == 'estrutura') n_est++;
  });
  document.getElementById('c1').textContent = tot_salvo.toLocaleString('pt-BR');
  document.getElementById('c2').textContent = n_salvo;
  document.getElementById('c3').textContent = n_est;
  document.getElementById('c4').textContent = n_erro;
  document.getElementById('c5').textContent = regs.length;

  var tb = document.querySelector('#tbl tbody');
  tb.innerHTML = '';
  regs.forEach(function(x){
    var tipo = x.tipo || '';
    var tag = tipo == 'salvo' ? 'salvo' : (tipo == 'erro' ? 'erro' : (tipo == 'estrutura' ? 'estrutura' : 'ciclo'));
    var tab = x.tabela ? esc(x.tabela) : '';
    var reg = x.registros ? x.registros.toLocaleString('pt-BR') : '';
    var det = '';
    if (x.acao) det = esc(x.acao) + (x.coluna ? ' ' + esc(x.coluna) : '');
    if (x.tipo_pg) det += ' ' + esc(x.tipo_pg);
    if (x.tipo_antes) det += ' (' + esc(x.tipo_antes) + ' -> ' + esc(x.tipo_depois) + ')';
    if (x.msg) det = esc(x.msg);
    if (x.data) det += (det?' | ':'') + 'data: ' + esc(x.data);
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>' + esc(x.hora) + '</td>' +
      '<td><span class="tag ' + tag + '">' + esc(tipo) + '</span></td>' +
      '<td>' + tab + '</td><td>' + reg + '</td><td class="mono">' + det + '</td>';
    tb.appendChild(tr);
  });
}

function mostrarJson(){
  var p = document.getElementById('json');
  if (p.style.display == 'none') p.style.display = 'block'; else p.style.display = 'none';
  if (p.textContent == '') atualizarJson();
}

async function atualizarJson(){
  var r = await fetch('/json?dia=' + atual);
  var regs = await r.json();
  document.getElementById('json').textContent = JSON.stringify(regs, null, 2);
}

async function copiarJson(){
  var r = await fetch('/json?dia=' + atual);
  var regs = await r.json();
  await navigator.clipboard.writeText(JSON.stringify(regs, null, 2));
  alert('JSON copiado!');
}

/* ---------- ABA CONCORRENTE ---------- */
async function carregarConcorrente(){
  var dia = document.getElementById('dia_c').value;
  var cod = document.getElementById('cod_c').value.trim();
  var r = await fetch('/concorrente?dia=' + dia + '&cod=' + encodeURIComponent(cod));
  var dados = await r.json();
  renderConcorrente(dados);
  carregarResumoConc();
}

async function carregarResumoConc(){
  var r = await fetch('/concorrente_resumo');
  var s = await r.json();
  if (s.erro) return;
  document.getElementById('cc1').textContent = (s.total||0).toLocaleString('pt-BR');
  document.getElementById('cc2').textContent = (s.produtos||0).toLocaleString('pt-BR');
  document.getElementById('cc3').textContent = s.min_data || '-';
  document.getElementById('cc4').textContent = s.max_data || '-';
}

function renderConcorrente(dados){
  if (dados.erro){
    document.getElementById('cc1').textContent = 'ERRO';
    var tb = document.querySelector('#tbl_c tbody');
    tb.innerHTML = '<tr><td colspan="12" style="color:#f87171">' + esc(dados.erro) + '</td></tr>';
    return;
  }
  document.getElementById('cc1').textContent = dados.length.toLocaleString('pt-BR');
  var tb = document.querySelector('#tbl_c tbody');
  tb.innerHTML = '';
  dados.forEach(function(x){
    var tr = document.createElement('tr');
    var url = x.url ? ('<a href="' + esc(x.url) + '" target="_blank" style="color:#38bdf8">' + esc(site(x.url)) + '</a>') : '';
    var siteEmp = x.site_empresa ? ('<a href="' + esc(x.site_empresa) + '" target="_blank" style="color:#38bdf8">' + esc(site(x.site_empresa)) + '</a>') : '';
    var loc = x.cidade ? (esc(x.cidade) + (x.estado ? '/' + esc(x.estado) : '')) : esc(x.estado || '');
    var fotoHtml = '';
    if (x.foto_local){
      fotoHtml = '<img src="/foto?p=' + encodeURIComponent(x.foto_local) + '" style="width:48px;height:48px;object-fit:cover;border-radius:6px;cursor:pointer" onclick="window.open(\'/foto?p=' + encodeURIComponent(x.foto_local) + '\',\'_blank\')">';
      if (x.foto_mega) fotoHtml += ' <a href="' + esc(x.foto_mega) + '" target="_blank" title="ver no MEGA" style="color:#f59e0b;font-size:11px">MEGA</a>';
    } else if (x.foto_mega){
      fotoHtml = '<a href="' + esc(x.foto_mega) + '" target="_blank" style="color:#f59e0b;font-size:11px">MEGA</a>';
    }
    tr.innerHTML = '<td>' + fotoHtml + '</td>' +
      '<td>' + esc(x.produto_codigo) + '</td>' +
      '<td>' + esc(x.produto_nome) + '</td>' +
      '<td>' + esc(x.ean) + '</td>' +
      '<td>' + esc(x.ean3) + '</td>' +
      '<td>' + esc(x.concorrente) + '</td>' +
      '<td style="color:#4ade80;font-weight:700">' + brl(x.preco) + '</td>' +
      '<td>' + brl(x.preco_avista) + '</td>' +
      '<td>' + brl(x.preco_pix) + '</td>' +
      '<td>' + url + ' ' + siteEmp + '</td>' +
      '<td>' + loc + '</td>' +
      '<td>' + esc(x.data_preco) + '</td>';
    tb.appendChild(tr);
  });
}

function site(url){
  try { return new URL(url).hostname.replace('www.',''); } catch(e) { return url; }
}

async function copiarConcJson(){
  var dia = document.getElementById('dia_c').value;
  var cod = document.getElementById('cod_c').value.trim();
  var r = await fetch('/concorrente?dia=' + dia + '&cod=' + encodeURIComponent(cod));
  var dados = await r.json();
  await navigator.clipboard.writeText(JSON.stringify(dados, null, 2));
  alert('JSON copiado!');
}

loadDias().then(function(){ recarregar(); setInterval(recarregar, 300000); setInterval(carregarConcorrente, 300000); });
document.getElementById('dia').addEventListener('change', recarregar);
document.getElementById('dia_c').addEventListener('change', carregarConcorrente);
document.getElementById('cod_c').addEventListener('keydown', function(e){ if (e.key == 'Enter') carregarConcorrente(); });
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == '/' or self.path.startswith('/?'):
                self._send(200, PAGE.encode('utf-8'), 'text/html')
            elif self.path == '/dias':
                self._send(200, json.dumps(listar_dias()).encode('utf-8'), 'application/json')
            elif self.path.startswith('/json'):
                q = self.path.split('?', 1)[1] if '?' in self.path else ''
                dia = ''
                for part in q.split('&'):
                    if part.startswith('dia='):
                        dia = part[4:]
                if not dia:
                    dias = listar_dias()
                    dia = dias[0] if dias else ''
                self._send(200, json.dumps(ler_dia(dia), ensure_ascii=False).encode('utf-8'), 'application/json')
            elif self.path == '/concorrente_produtos':
                self._send(200, json.dumps(concorrente_produtos(), ensure_ascii=False).encode('utf-8'), 'application/json')
            elif self.path == '/concorrente_resumo':
                self._send(200, json.dumps(concorrente_resumo(), ensure_ascii=False).encode('utf-8'), 'application/json')
            elif self.path.startswith('/foto'):
                q = self.path.split('?', 1)[1] if '?' in self.path else ''
                p = ''
                for part in q.split('&'):
                    if part.startswith('p='):
                        p = part[2:]
                from urllib.parse import unquote
                p = unquote(p)
                if p and os.path.exists(p) and p.lower().endswith(('.webp', '.jpg', '.png', '.jpeg')):
                    with open(p, 'rb') as f:
                        self._send(200, f.read(), 'image/webp')
                else:
                    self._send(404, b'not found', 'text/plain')
            elif self.path.startswith('/concorrente'):
                q = self.path.split('?', 1)[1] if '?' in self.path else ''
                dia = ''
                cod = ''
                for part in q.split('&'):
                    if part.startswith('dia='):
                        dia = part[4:]
                    elif part.startswith('cod='):
                        cod = part[4:]
                dados = concorrente_dia(dia)
                if cod:
                    if isinstance(dados, list):
                        dados = [d for d in dados if str(d.get('produto_codigo')) == cod]
                self._send(200, json.dumps(dados, ensure_ascii=False).encode('utf-8'), 'application/json')
            else:
                self._send(404, b'not found', 'text/plain')
        except Exception as e:
            try:
                self._send(500, str(e).encode('utf-8'), 'text/plain')
            except Exception:
                pass

def main():
    srv = HTTPServer(('127.0.0.1', PORT), Handler)
    srv.serve_forever()

if __name__ == '__main__':
    main()
