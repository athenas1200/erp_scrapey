# -*- coding: utf-8 -*-
"""Conexao com o banco 'postgres' da VPS (schema memory_*), via tunel porta 15436.
Este e o banco de memoria - separado do s9_real (replica) que fica na porta 15435.
"""
import paramiko, socket, threading, psycopg2

VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"
LOCAL_PORT = 15436


def connect():
    tunnel = paramiko.SSHClient()
    tunnel.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    tunnel.connect(VPS_HOST, username=VPS_USER, password=VPS_PWD, timeout=30)
    tr = tunnel.get_transport()
    local = socket.socket()
    local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local.bind(("127.0.0.1", LOCAL_PORT)); local.listen(5)
    def fwd():
        while True:
            try: client, _ = local.accept()
            except Exception: break
            ch = tr.open_channel("direct-tcpip", ("127.0.0.1", 5434), client.getpeername())
            def pipe(s, d):
                try:
                    while True:
                        b = s.recv(65536)
                        if not b: break
                        d.sendall(b)
                except Exception: pass
                try: d.shutdown(2)
                except Exception: pass
            threading.Thread(target=pipe, args=(client, ch), daemon=True).start()
            threading.Thread(target=pipe, args=(ch, client), daemon=True).start()
    threading.Thread(target=fwd, daemon=True).start()
    conn = psycopg2.connect(host="127.0.0.1", port=LOCAL_PORT, dbname="postgres",
                            user="postgres", password=PG_PWD, connect_timeout=30)
    return tunnel, local, conn, conn.cursor()


def close_all(tunnel, local, conn):
    try: conn.close()
    except Exception: pass
    try: local.close()
    except Exception: pass
    try: tunnel.close()
    except Exception: pass
