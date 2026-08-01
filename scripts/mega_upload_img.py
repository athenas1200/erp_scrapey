# -*- coding: utf-8 -*-
"""Upload de uma imagem para o MEGA (pasta FotosS9) retornando o link publico.
Uso: python mega_upload_img.py <arquivo> [nome_opcional]
Saida (stdout): link publico ou linha vazia em erro.
Reusa uma unica instancia via subprocess a cada chamada (mais simples e robusto).
"""
import os, sys, time, json, io, threading
from mega import Mega

EMAIL = "bkp.2021romanatian@gmail.com"
SENHA = "Lin281168***"
MEGA_DIR = "FotosS9"

def main():
    arquivo = sys.argv[1]
    if not os.path.exists(arquivo):
        sys.stderr.write("arquivo nao existe: %s\n" % arquivo)
        sys.exit(1)
    nome = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(arquivo)

    try:
        mega = Mega()
        m = mega.login(EMAIL, SENHA)
        dest = None
        for n, f in m.get_files().items():
            if f['a'] and f['a'].get('n') == MEGA_DIR and f['t'] == 1:
                dest = f; break
        if dest is None:
            m.create_folder(MEGA_DIR)
            time.sleep(1)
            for n, f in m.get_files().items():
                if f['a'] and f['a'].get('n') == MEGA_DIR and f['t'] == 1:
                    dest = f; break
        if dest is None:
            sys.stderr.write("pasta nao encontrada\n")
            sys.exit(1)

        m.upload(arquivo, dest['h'], dest_filename=nome)
        time.sleep(2)
        node = None
        for _ in range(30):
            for n, f in m.get_files().items():
                if f['t'] == 0 and f['a'] and f['a'].get('n') == nome and f['p'] == dest['h']:
                    node = (n, f); break
            if node:
                break
            time.sleep(1)
        if node is None:
            sys.stderr.write("arquivo nao localizado no MEGA\n")
            sys.exit(1)
        link = m.get_link(node)
        sys.stdout.write(link or '')
        sys.stdout.flush()
    except Exception as e:
        sys.stderr.write("ERRO MEGA: %s\n" % str(e)[:200])
        sys.exit(1)

if __name__ == '__main__':
    main()
