# -*- coding: utf-8 -*-
"""S9 MEMORY API - consulta o conhecimento do ERP (schema memory_* no db 'postgres').

Pergunta em linguagem natural -> resposta com tabelas/colunas/relacionamentos/regras.

Modos de uso:
  python memoria_api.py "onde fica o preco do produto?"    # CLI (uma pergunta)
  python memoria_api.py --interativo                        # loop de perguntas
  python memoria_api.py --http [porta]                      # servidor HTTP (integra com tela)
  python memoria_api.py --json "pergunta"                   # saida JSON pura (para scripts)

Saida: JSON {"pergunta", "resposta", "tabelas":[...], "colunas":[...], ...}
"""
import sys, io, os, json, re, time, unicodedata, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'memoria_api.log')

PALAVRAS_VAZIAS = set('''a o e de do da em com para por no na um uma os as se ao
como onde qual quais quando quem que eu tu ele ela voce nos qual eh sao esta
estao tem ha existe existem preciso quero saber gostaria por favor tabela campo
coluna dado registro codigo valor preco qual a o que'''.split())


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        with io.open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def sem_acentos(s):
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode()


def tokens(pergunta):
    """Extrai termos de busca da pergunta (min 3 chars, sem acento, minusculo)."""
    p = sem_acentos(pergunta.lower())
    p = re.sub(r'[^a-z0-9 ]+', ' ', p)
    return [t for t in p.split() if len(t) >= 3 and t not in PALAVRAS_VAZIAS]


def abrir():
    from db_mem import connect, close_all
    return connect(), close_all


class MemoriaAPI:
    def __init__(self):
        self._conn = None
        self._close = None

    def _cur(self):
        if self._conn is None:
            (tr, l, conn, cur), self._close = abrir()
            self._conn = conn
            self._cur_obj = cur
            self._tunnel = tr
            self._local = l
        return self._cur_obj

    def fechar(self):
        if self._conn is not None:
            try:
                self._close(self._tunnel, self._local, self._conn)
            except Exception:
                pass
            self._conn = None

    def q(self, sql, args=None):
        cur = self._cur()
        cur.execute(sql, args or ())
        return cur.fetchall()

    # ---------------- buscas (com score de relevancia) ----------------
    @staticmethod
    def _score_nome(nome, termo):
        """Score de correspondencia nome x termo: exato > prefixo > contem."""
        n = sem_acentos(nome.lower())
        t = sem_acentos(termo.lower())
        if n == t:
            return 100
        if n.startswith(t):
            return 80
        if t.startswith(n):
            return 60
        if t in n:
            return 50
        if n in t:
            return 30
        return 0

    def buscar_tabelas(self, termos):
        pool = self.q("""SELECT tabela, modulo, colunas, linhas, descricao, confianca
            FROM memory_tables WHERE tabela ILIKE ANY(%s) OR modulo ILIKE ANY(%s)
            OR descricao ILIKE ANY(%s)""",
                      (["%" + t + "%" for t in termos],
                       ["%" + t + "%" for t in termos],
                       ["%" + t + "%" for t in termos]))
        scored = []
        for r in pool:
            s = sum(self._score_nome(r[0], t) for t in termos) * 2
            s += sum(self._score_nome(r[1] or '', t) for t in termos)
            s += sum(1 for t in termos if t in sem_acentos(r[4] or '').lower())
            s += 0.001 * (r[3] or 0)   # desempate por tamanho (importancia)
            scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored if _ > 0][:15]

    def buscar_colunas(self, termos):
        pool = self.q("""SELECT tabela, coluna, tipo, significado, is_pk, confianca
            FROM memory_columns
            WHERE coluna ILIKE ANY(%s) OR significado ILIKE ANY(%s)
            ORDER BY is_pk DESC, coluna""",
                      (["%" + t + "%" for t in termos],
                       ["%" + t + "%" for t in termos]))
        scored = []
        for r in pool:
            s = sum(self._score_nome(r[1], t) for t in termos) * 3   # nome da coluna
            s += sum(self._score_nome(r[3] or '', t) for t in termos)  # significado
            s += sum(1 for t in termos if t in sem_acentos(r[0]).lower())  # tabela
            scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return self._dedup([r for _, r in scored if _ > 0], chave_idx=(0, 1))[:30]

    def buscar_entidades(self, termos):
        pool = self.q("""SELECT entidade, descricao, tabelas_principais, confianca
            FROM memory_entities""")
        scored = []
        for r in pool:
            nome = sem_acentos(r[0].lower())
            desc = sem_acentos(r[1] or '').lower()
            tabs = sem_acentos(json.dumps(r[2]) if not isinstance(r[2], str) else (r[2] or '')).lower()
            s = sum(self._score_nome(r[0], t) for t in termos) * 2
            s += sum(1 for t in termos if t in desc or t in tabs)
            scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored if _ > 0][:10]

    def buscar_relacionamentos(self, termos, tab=None):
        res = []
        if tab:
            res += self.q("""SELECT tabela, coluna, ref_tabela, ref_coluna, tipo, confianca
                FROM memory_relationships WHERE tabela = %s OR ref_tabela = %s LIMIT 25""",
                          (tab, tab))
        for t in termos:
            res += self.q("""SELECT tabela, coluna, ref_tabela, ref_coluna, tipo, confianca
                FROM memory_relationships
                WHERE tabela ILIKE %s OR ref_tabela ILIKE %s OR coluna ILIKE %s
                LIMIT 20""", ('%' + t + '%', '%' + t + '%', '%' + t + '%'))
        return self._dedup(res)

    def buscar_regras(self, termos, tab=None):
        res = []
        if tab:
            res += self.q("""SELECT tabela, regra, significado, confianca
                FROM memory_business_rules WHERE tabela = %s LIMIT 30""", (tab,))
        for t in termos:
            res += self.q("""SELECT tabela, regra, significado, confianca
                FROM memory_business_rules
                WHERE regra ILIKE %s OR significado ILIKE %s LIMIT 15""",
                          ('%' + t + '%', '%' + t + '%'))
        return self._dedup(res)

    def buscar_documentos(self, tab=None):
        if not tab:
            return []
        return self.q("""SELECT nome, conteudo FROM memory_documents WHERE nome = %s""", (tab,))

    def buscar_semantica(self, termos):
        res = []
        for t in termos:
            res += self.q("""SELECT entidade, pergunta, resposta, confianca
                FROM memory_semantics
                WHERE entidade ILIKE %s OR pergunta ILIKE %s OR resposta ILIKE %s LIMIT 10""",
                          ('%' + t + '%', '%' + t + '%', '%' + t + '%'))
        return self._dedup(res)

    @staticmethod
    def _dedup(res, chave_idx=0):
        vistos, out = set(), []
        for r in res:
            k = r[chave_idx] if isinstance(chave_idx, int) else tuple(r[i] for i in chave_idx)
            if k not in vistos:
                vistos.add(k)
                out.append(r)
        return out

    # ---------------- resposta final ----------------
    def responder(self, pergunta):
        termos = tokens(pergunta)
        log("pergunta: %s | termos: %s" % (pergunta, termos))
        if not termos:
            return {"erro": "pergunta vazia"}

        tabs = self.buscar_tabelas(termos)
        cols = self.buscar_colunas(termos)
        ents = self.buscar_entidades(termos)
        regras = self.buscar_regras(termos)
        sem = self.buscar_semantica(termos)

        # determina a tabela principal (mais citada / mais importante)
        tab_principal = tabs[0] if tabs else None

        rel = self.buscar_relacionamentos(termos, tab=tab_principal[0] if tab_principal else None)
        docs = self.buscar_documentos(tab_principal[0]) if tab_principal else []

        linhas = []
        if ents:
            for ent, desc, tabs_princ, conf in ents[:3]:
                try:
                    tlista = json.loads(tabs_princ) if isinstance(tabs_princ, str) else (tabs_princ or [])
                except Exception:
                    tlista = []
                linhas.append("Entidade **%s**: %s (tabelas: %s)" % (ent, desc, ", ".join(tlista[:5])))
        if tab_principal:
            tab, mod, ncol, nlin, desc, conf = tab_principal
            linhas.append("Tabela **%s** (modulo %s, %s colunas, ~%s registros): %s" %
                          (tab, mod, ncol, nlin, desc))
        if cols:
            for tab, col, tipo, sign, ispk, conf in cols[:8]:
                linhas.append("- `%s.%s` (%s%s): %s" %
                              (tab, col, tipo, " PK" if ispk else "", sign or ""))
        if rel:
            linhas.append("Relacionamentos:")
            for tab, col, ref, refcol, tipo, conf in rel[:8]:
                linhas.append("  - `%s.%s` -> `%s.%s`" % (tab, col, ref, refcol))
        if regras:
            linhas.append("Regras:")
            for tab, regra, sign, conf in regras[:6]:
                linhas.append("  - %s: %s" % (tab, regra))
        if docs:
            linhas.append("Documentacao: tabela %s documentada (markdown)." % tab_principal[0])

        resposta = "\n".join(linhas) if linhas else (
            "Nao encontrei informacao na memoria sobre '%s'. "
            "Dicas: use nomes como 'cliente', 'produto', 'venda', 'preco', "
            "'estoque', 'nfe', 'financeiro'." % pergunta)

        return {
            "pergunta": pergunta,
            "resposta": resposta,
            "tabelas": [{"tabela": r[0], "modulo": r[1], "colunas": r[2], "linhas": r[3],
                         "descricao": r[4], "confianca": r[5]} for r in tabs[:10]],
            "colunas": [{"tabela": r[0], "coluna": r[1], "tipo": r[2], "significado": r[3],
                         "is_pk": r[4]} for r in cols[:20]],
            "entidades": [{"entidade": r[0], "descricao": r[1], "tabelas": r[2]} for r in ents[:5]],
            "relacionamentos": [{"tabela": r[0], "coluna": r[1], "ref_tabela": r[2],
                                 "ref_coluna": r[3], "tipo": r[4], "confianca": r[5]} for r in rel[:20]],
            "regras": [{"tabela": r[0], "regra": r[1], "significado": r[2]} for r in regras[:15]],
            "docs": [{"nome": r[0]} for r in docs[:5]],
        }


def modo_http(porta):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    api = MemoriaAPI()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            try:
                p = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(p.query)
                pergunta = (q.get('q') or [''])[0].strip()
                if p.path == '/health':
                    body = json.dumps({"status": "ok"}).encode()
                    self.send_response(200)
                elif not pergunta:
                    body = json.dumps({"erro": "use ?q=pergunta"}).encode()
                    self.send_response(200)
                else:
                    body = json.dumps(api.responder(pergunta), ensure_ascii=False, indent=1).encode()
                    self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"erro": str(e)[:300]}).encode()
                try:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception:
                    pass

    log("Memoria API HTTP em http://127.0.0.1:%d  (?q=pergunta)" % porta)
    ThreadingHTTPServer(('127.0.0.1', porta), H).serve_forever()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == '--http':
        porta = int(args[1]) if len(args) > 1 else 8091
        modo_http(porta)
        return
    if args[0] == '--interativo':
        api = MemoriaAPI()
        print("Memoria S9. Digite sua pergunta (ou 'sair').")
        while True:
            try:
                p = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not p or p.lower() in ('sair', 'exit', 'quit'):
                break
            try:
                r = api.responder(p)
                print("\n" + r["resposta"] + "\n")
            except Exception as e:
                print("ERRO: %s" % str(e)[:200])
        api.fechar()
        return
    # modo CLI / --json
    pergunta = args[0] if not args[0].startswith('--') else args[1]
    if args[0] == '--json':
        pergunta = args[1]
    api = MemoriaAPI()
    try:
        r = api.responder(pergunta)
    finally:
        api.fechar()
    if '--json' in args:
        sys.stdout.write(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(r["resposta"])
        print()
        if r["tabelas"]:
            print("Tabelas (%d):" % len(r["tabelas"]))
            for t in r["tabelas"][:10]:
                print("  - %s [%s] (%s colunas, ~%s linhas)" %
                      (t["tabela"], t["modulo"], t["colunas"], t["linhas"]))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log("ERRO memoria_api: %s" % str(e)[:300])
        try:
            sys.stderr.write("ERRO: %s\n" % str(e)[:300])
        except Exception:
            pass
