# -*- coding: utf-8 -*-
"""Extrai ate 4 fotos por produto da Prod_Serv_Fotos e salva em .webp.
Fonte: tabela Prod_Serv_Fotos (coluna Foto = base64/JPEG).
Destino: pasta FOTOS/<Codigo>_<Nome_limpo>/<1..4>.webp
"""
import pyodbc, sys, os, io, base64, time
from PIL import Image

BASE = r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode'
sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

OUT = BASE + r'\FOTOS'
MAX_FOTOS = 4
os.makedirs(OUT, exist_ok=True)

def limpar_nome(nome):
    invalidos = '<>:"/\\|?*'
    for ch in invalidos:
        nome = nome.replace(ch, '_')
    return nome.strip()[:60]

def salvar_webp(caminho, dados):
    try:
        img = Image.open(io.BytesIO(dados))
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        img.save(caminho, 'WEBP', quality=85)
        return True
    except Exception as e:
        return str(e)[:60]

def main():
    sconn = pyodbc.connect(sync.SQL_DSN, timeout=60)
    scur = sconn.cursor()
    # pega produtos que tem fotos
    scur.execute("""SELECT p.Codigo, p.Nome, f.Foto, f.Posicao, f.Principal
        FROM dbo.Prod_Serv_Fotos f
        JOIN dbo.Prod_Serv p ON p.Ordem = f.Ordem_Prod_Serv
        WHERE f.Principal = 1 OR f.Posicao < 4
        ORDER BY p.Codigo, f.Principal DESC, f.Posicao""")
    total = 0
    atuais = {}
    for r in scur.fetchall():
        codigo, nome, foto, posicao, principal = r
        if not foto or len(str(foto)) < 50:
            continue
        if codigo not in atuais:
            atuais[codigo] = (nome, [])
        if len(atuais[codigo][1]) >= MAX_FOTOS:
            continue
        atuais[codigo][1].append((posicao, str(foto)))

    print("Produtos com fotos:", len(atuais))
    for codigo, (nome, fotos) in sorted(atuais.items()):
        pasta = os.path.join(OUT, "%s_%s" % (codigo, limpar_nome(nome)))
        os.makedirs(pasta, exist_ok=True)
        n_salvas = 0
        for i, (posicao, foto_b64) in enumerate(fotos, start=1):
            try:
                dados = base64.b64decode(foto_b64)
            except Exception:
                continue
            caminho = os.path.join(pasta, "%d.webp" % i)
            r = salvar_webp(caminho, dados)
            if r is True:
                n_salvas += 1
            else:
                print("  ERRO %s foto %d: %s" % (codigo, i, r))
        total += n_salvas
        if n_salvas:
            print("  %s | %s | %d fotos" % (codigo, nome[:50], n_salvas))
    sconn.close()
    print("\nTOTAL FOTOS SALVAS (webp):", total)

if __name__ == '__main__':
    main()
