# AGENTS.md — Memória do Projeto

Guia permanente para agentes que trabalham neste repositório (ERP Scrapey).

## Objetivo
Integrar o ERP S9: sincronizar SQL Server → PostgreSQL (VPS), monitorar preços
concorrentes diariamente (scraper Firecrawl), baixar fotos, enviar ao MEGA e
gerar relatórios por e-mail.

## Conexões / Credenciais
- **VPS**: 84.247.189.155, root, porta PG 5434. Túnel SSH (paramiko) → porta local **15434**.
- **PostgreSQL**: db `s9_real`, user `postgres`. Acesso via túnel. `session_replication_role = replica` para inserir com FK desativada.
- **SQL Server**: `192.168.0.101,1435`, db `S9_Real`. Conexão via DSN pyodbc.
- **MEGA**: conta `bkp.2021romanatian@gmail.com`, pasta `FotosS9`. `mega.py` requer `tenacity==8.2.3` (Python 3.14).
- **Firecrawl**: MCP keyless (`npx firecrawl-mcp`) — tem rate limit por IP (backoff 60s).

## Estrutura
```
scripts/sincronizador/   sync_silencioso.py (loop 5min), tela_log_server.py (porta 8090)
scripts/coletor_precos/  coletor_lote.py (scraper 2332 produtos), firecrawl_batch.py (batch MCP)
scripts/fotos/           fotos_webp.py (extração .webp de Prod_Serv_Fotos)
scripts/mega/            mega_etapa.py (lotes), mega_upload_img.py (link público)
scripts/relatorio/       email_diario.py (00:00, só se houver alterações)
scripts/migracao/        mig_data.py (migração auditada)
```

## Regras de negócio (importantes!)
1. **Scraper por produto**: até **3 preços de 3 sites distintos**. Concorrentes fixos:
   Mercado Livre, Shopmedical, Fisio Store. Fallback: Google (Firecrawl search).
2. **Link mapeado**: passadas seguintes usam scrape do link salvo (rápido). Se o link
   não tiver preço (sem estoque — raro), procura OUTRA empresa.
3. **Histórico**: cada coleta cria registro NOVO (nunca sobrescreve). `data_coleta` = NOW(),
   `data_preco` = dia. Isso gera histórico de preços por data/hora.
4. **Fotos**: máximo **4 fotos por produto** (`MAX_FOTOS = 4`). Se já tiver 4 na pasta
   `FOTOS_CONC\<codigo>`, não baixa mais.
5. **Fotos → MEGA**: salva primeiro na máquina, depois upload em lotes (`mega_etapa.py`)
   para evitar rate limit. Link salvo em `concorrente.foto_mega`.
6. **Checkpoint persistente** (`logs/coleta_estado.json`): se a máquina reiniciar, volta de
   onde parou. Ao completar ciclo, zera e recomeça (histórico diário).
7. **Tarefas agendadas**: `S9_Coleta_Precos` (23:00, pythonw silencioso), `S9_Relatorio_Diario` (00:00).

## Tabela `concorrente` (PostgreSQL)
Colunas: `id, produto_ordem, produto_codigo, produto_nome, ean, ean3, concorrente,
url, preco, preco_avista, preco_pix, site_empresa, cidade, estado, foto_local,
foto_mega, disponivel, data_coleta, data_preco`.

## Tela web (tela_log_server.py)
- Aba Log Diário (sync) e aba **Concorrente** (preços/fotos).
- Endpoints: `/json?dia=`, `/concorrente?dia=&cod=`, `/concorrente_resumo`,
  `/concorrente_produtos`, `/foto?p=caminho`.
- Colunas concorrente: Foto | Codigo | Produto | EAN | EAN3 | Empresa | Preço | À vista | Pix | Site | Cidade | Data.

## Comandos úteis
- Rodar scraper completo silencioso: `pythonw coletor_lote.py 0`
- Rodar lote teste (1 produto): `python coletor_lote.py 1`
- Upload MEGA em lotes: `python mega_etapa.py 10`
- Testar/validar conexões e banco via scripts `util/*` e `ver_*.py`.

## Observações técnicas
- `psycopg2.execute()` retorna None — usar `fetchall()`.
- SQL Server limita 2100 parâmetros por consulta.
- Nomes de coluna PG > 63 chars são truncados.
- Python 3.14: `tenacity` quebrado em versão nova — fix com `tenacity==8.2.3`.
- Arquivos de trabalho em `C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\`.
