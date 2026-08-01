# -*- coding: utf-8 -*-
"""FIREWCRawL BATCH - abre UMA sessao MCP e executa varias buscas.
Recebe via stdin um JSON: {"queries": [{"id": 0, "query": "..."}], "delay": 1.0}
Retorna via stdout um JSON: {"results": {id: [blocos...]}}
Com backoff em rate limit (429) - espera 60s e re-tenta ate N vezes.
"""
import sys, json, time, io
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio

def main():
    entrada = json.load(sys.stdin)
    queries = entrada['queries']
    delay = float(entrada.get('delay', 1.0))
    tentativas_max = int(entrada.get('tentativas', 5))

    async def run():
        params = StdioServerParameters(command='npx', args=['-y', 'firecrawl-mcp'])
        resultados = {}
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for item in queries:
                    qid = item['id']
                    q = item['query']
                    for tent in range(tentativas_max):
                        try:
                            r = await asyncio.wait_for(
                                session.call_tool('firecrawl_search',
                                    {'query': q, 'limit': 5, 'lang': 'pt', 'country': 'br'}),
                                timeout=60)
                            blocos = []
                            for c in r.content:
                                blocos.append(getattr(c, 'text', str(c)))
                            resultados[qid] = blocos
                            break
                        except Exception as e:
                            msg = str(e).lower()
                            if '429' in msg or 'rate' in msg or 'too many' in msg:
                                sys.stderr.write("rate limit, aguardando 60s...\n")
                                sys.stderr.flush()
                                await asyncio.sleep(60)
                            else:
                                resultados[qid] = []
                                break
                        finally:
                            pass
                    else:
                        resultados[qid] = []
                    await asyncio.sleep(delay)
        return resultados

    res = asyncio.run(run())
    sys.stdout.write(json.dumps({'results': res}))
    sys.stdout.flush()

if __name__ == '__main__':
    main()
