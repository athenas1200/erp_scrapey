# Lógica do Sistema — ERP Scrapey

Fluxo completo do sistema: sincronização, coleta de preços concorrentes, fotos, MEGA e relatórios.

## 1. Sincronização (contínua)

```
SQL Server (S9_Real) ──loop 5 min──> PostgreSQL (VPS, s9_real)
    • dados novos inseridos por PK
    • estrutura sincronizada (ADD/ALTER COLUMN)
    • log diário JSON em logs/sync_AAAA-MM-DD.json
    • tela web (porta 8090) mostra o log
```

- `sync_silencioso.py` — sincronizador principal (loop 5 min)
- `tela_log_server.py` — dashboard web local

## 2. Scraper de preços concorrentes (diário, 23h)

```
Para cada produto (2.332 com EAN):
        │
        ▼
 Já tem link salvo? ──sim──> Firecrawl scrape do link (RÁPIDO)
        │ não                    │ preço achou? sim → atualiza registro
        ▼                        │ não (sem estoque / raro)
 Busca 3 concorrentes           └──> procura OUTRA empresa que tenha o link
 (Mercado Livre, Shopmedical, Fisio Store)
        │
        ▼
 Se ainda faltar → fallback Google (busca geral)
        │
        ▼
 Até ter 3 preços de 3 sites distintos
        │
        ▼
 Baixa a foto do produto da página do concorrente
        │
        ▼
 Salva foto em pasta local (FOTOS_CONC\<Codigo>\<site>.webp)
        │
        ▼
 Grava no banco: preço, à vista, pix, empresa, url, ean, ean3,
                  data_coleta, data_preco, caminho da foto local
```

### Ponto-chave do desempenho
- **1ª passada (lenta):** busca no Google/Firecrawl + scrape da página + download da foto.
- **Passadas seguintes (rápida):** usa o **link salvo** direto (Firecrawl scrape), sem nova busca.
- Se o link não tiver mais preço (empresa sem estoque), procura **outra empresa** que tenha.

### Persistência / recuperação
- **Checkpoint persistente** (`logs/coleta_estado.json`): se a máquina desligar/reiniciar, o coletor volta exatamente de onde parou.
- Ao completar todos os produtos, o estado zera e o ciclo recomeça no dia seguinte (gera histórico diário com data/hora de cada coleta).

## 3. Fotos → MEGA (upload em etapas)

```
Foto local (FOTOS_CONC\...) 
    │
    ▼
mega_etapa.py sobe em lotes (ex: 10 por execução)
    │
    ▼
Link público gerado (conta bkp.2021romanatian@gmail.com, pasta FotosS9)
    │
    ▼
Grava link em concorrente.foto_mega
```

- Upload em **etapas/lotes** para evitar problemas de conexão e rate limit do MEGA.

## 4. Relatório diário por e-mail

```
Diariamente 00:00 (tarefa agendada S9_Relatorio_Diario)
    • verifica dados alterados (Data_Alteracao) + layout alterado (colunas)
    • envia e-mail somente se houver alterações
    • duas seções: Dados alterados | Layout alterado
```

## 5. Tela web (localhost:8090)

- **Aba Log Diário:** registros salvos, erros, alterações de estrutura, por dia.
- **Aba Concorrente:** para cada produto, os 3 preços dos concorrentes com:
  - foto (local / link MEGA)
  - código, produto, EAN, EAN3
  - empresa, site, cidade/UF
  - preço, à vista, pix
  - data da coleta
  - filtro por código do produto
  - atualização automática a cada 5 min

## Diagrama resumido

```
[SQL Server] ──sync 5min──> [PostgreSQL VPS] ──tela web──> [navegador 8090]
                                  │
                                  ├──> [Firecrawl] busca/scrape preços (diário 23h)
                                  │         └──> fotos locais ──> [MEGA] links
                                  └──> [E-mail diário] 00:00 se houver alterações
```
