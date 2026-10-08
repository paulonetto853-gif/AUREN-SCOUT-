# AUREN SCOUT

Agente independente de pesquisa e qualificação comercial. O Scout coleta e organiza evidências públicas, audita sinais técnicos observáveis de sites e calcula uma pontuação determinística. Ele não decide contato, não envia mensagens e não se integra ao WhatsApp nem ao Automaton.

## Arquitetura

- `scout/discovery.py`: contrato do provedor e catálogo sintético de demonstração.
- `scout/research.py`: valida fontes dos dados, executa auditoria de site e calcula score.
- `scout/website.py`: auditoria HTTP/HTML, com limites de resposta, validação de hosts públicos e respeito a `robots.txt`.
- `scout/scoring.py`: pontuação e classificação sem dependência de rede ou do provedor.
- `scout/storage.py`: armazenamento em memória, protegido para uso pela API concorrente.
- `scout/service.py`: coordenação, ordenação e logs da pesquisa.
- `scout/api.py` e `scout/cli.py`: interface HTTP e inicialização.
- `scout/browser_controller.py`: conexão e controle isolado do Microsoft Edge via CDP.

O Scout usa a biblioteca padrão do Python, exceto pelo Playwright, necessário para conectar ao endpoint CDP existente. Ele não instala nem inicia outro navegador.

## Instalação e execução

Requer Python 3.10 ou superior. Instale a dependência do Browser Controller:

```sh
python3 -m pip install -r requirements.txt
```

Inicie a API do Scout como antes:

```sh
python3 -m scout
```

A API inicia em `http://127.0.0.1:8080`; Ctrl+C encerra o servidor. As variáveis são lidas do ambiente do processo; `.env.example` é uma referência e não é carregado automaticamente.

## Executável Windows

Gerar no Windows (o PyInstaller não faz cross-build):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-exe.ps1
```

O resultado é `dist\AurenScout-V2.exe`. O nome separado evita sobrescrever um `AurenScout.exe` preexistente. Executar:

```powershell
.\dist\AurenScout-V2.exe
```

O projeto mantém duas instâncias isoladas: **V1 = Fortaleza = CDP `127.0.0.1:9222`**, com o perfil `EdgeProfile`; **V2 = AIVIO = CDP `127.0.0.1:9223`**, com o perfil `EdgeProfile-V2`. O V2 aceita somente `http://127.0.0.1:9223` e não se conecta ao endpoint da V1. Os scripts de inicialização são separados e não reutilizam perfis.

O processo mantém a API local ativa até Ctrl+C. Verificar em outro PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

A resposta deve indicar `status: ok` e `operational: true`. Para desenvolvimento no Windows, use `.\scripts\run-scout.ps1`.

O Browser Controller é executado sob demanda, sem abrir ou autenticar o Edge. O usuário deve iniciar o Microsoft Edge com depuração remota habilitada, entrar manualmente no site desejado e manter o navegador aberto. Depois, consulte o estado via CDP:

```sh
python3 -m scout browser-status
```

O V2 usa `http://127.0.0.1:9223` por padrão. Para a instância V1, use o executável V1 já instalado e o endpoint `http://127.0.0.1:9222`.

Para solicitar explicitamente uma navegação na aba ativa:

```sh
python3 -m scout browser-navigate https://example.com/
```

O comando de status retorna conexão, navegador, abas com título/URL e aba ativa em JSON. Navegação aceita somente URLs HTTP(S) sem credenciais; nenhuma URL é aberta automaticamente. Não exponha o endpoint CDP a redes não confiáveis.

Para diagnosticar somente a página atualmente ativa da V2, sem clique, preenchimento ou navegação:

```powershell
.\dist\AurenScout-V2.exe browser-inspect
```

O comando retorna JSON com título, URL, texto visível (limitado a 12.000 caracteres), inputs visíveis não ocultos/não password, botões e links. Valores de campos identificados como sensíveis não são incluídos, e query strings/fragments de URLs são removidos.

Para inspecionar os atributos dos campos e abrir apenas o dropdown “Escolha o ramo” para diagnóstico (sem selecionar opção ou acionar “Buscar”):

```powershell
.\dist\AurenScout-V2.exe browser-inspect-category
```

O comando retorna os inputs visíveis e seus atributos/labels, destaca o segundo input text sem placeholder, e compara os elementos DOM antes/depois de abrir o dropdown. A única interação é clicar exatamente uma vez no botão único “Escolha o ramo”; não preenche campos nem seleciona opções.

## Execução local no Windows

O comando que controla o Edge precisa rodar no mesmo Windows do navegador. Um terminal do GitHub Codespaces, WSL ou outro container não compartilha o `127.0.0.1` do Windows; execute estes passos em um PowerShell local, dentro de uma cópia do repositório clonada no Windows.

1. Instale Python para Windows conforme a política da sua máquina e abra PowerShell na pasta do repositório.
2. Instale as dependências do projeto:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Para iniciar o Edge da V1 (Fortaleza), mantenha o script e o perfil existentes:

   ```powershell
   .\scripts\start-edge-cdp.ps1
   ```

   Esse script mantém a V1 em `127.0.0.1:9222` e usa `%LOCALAPPDATA%\AurenScout\EdgeProfile`. Não o altere para iniciar a V2.

4. Para iniciar o Edge da V2 (AIVIO), use exclusivamente o script novo:

   ```powershell
   .\scripts\start-edge-cdp-v2.ps1
   ```

   O script V2 fixa CDP em `127.0.0.1:9223` e o perfil em `C:\Users\gabriela.pacheco\AppData\Local\AurenScout\EdgeProfile-V2`. Ele não lê, altera ou reutiliza `EdgeProfile`. Faça login manualmente no perfil V2 e mantenha o Edge aberto. Depois, verifique e consulte o Edge V2:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:9223/json/version | ConvertTo-Json -Depth 4
   Remove-Item Env:SCOUT_CDP_ENDPOINT -ErrorAction SilentlyContinue
   .\dist\AurenScout-V2.exe browser-status
   ```

   O `browser-status` do V2 usa `http://127.0.0.1:9223` quando `SCOUT_CDP_ENDPOINT` não está definido e rejeita o endpoint V1. O resultado deve conter `"connected": true`, `"browser": "Microsoft Edge"`, `tabs` (título e URL de cada aba) e `activeTab`.

   Para executar `browser-status` V1 com a aplicação V1 já instalada, mantenha o endpoint em `http://127.0.0.1:9222`; não use o executável V2 para se conectar ao Edge V1. Se o projeto estiver em Codespaces, faça um clone local no Windows e execute os comandos a partir dele — não execute o cliente CDP no terminal remoto.

O script não cria túneis, não altera firewall e não autentica no AIVIO. O endpoint CDP dá controle elevado sobre a instância do navegador: mantenha-o vinculado ao loopback, não encaminhe a porta e não o exponha à rede/internet. Se a porta estiver ocupada ou o Edge não puder ser localizado pelo registro, o script para com erro em vez de escolher outro executável ou iniciar outro navegador.

## Integração AIVIO (V2)

O V2 recebe tarefas explícitas do AUREN, usa a aba ativa do Edge via CDP local e devolve JSON padronizado. O login no AIVIO continua manual. O V2 não decide quando gerar site nem executa ações comerciais.

Antes de executar tarefas, inicie o Edge CDP como descrito acima, abra o AIVIO na aba ativa e faça login manualmente. A pesquisa exige campos acessíveis de cidade, estado e categoria, e o perfil/ação de geração só é aberto quando solicitado na tarefa. Os nomes e URLs de leads são observações do AIVIO; campos não disponíveis permanecem `null`. A paginação usa controles visíveis com rótulos acessíveis reconhecíveis, e a geração usa apenas ações visíveis identificadas por seus rótulos acessíveis.

### API de tarefas

`POST /tasks` aceita `SEARCH_LEADS`, `OPEN_COMPANY`, `GENERATE_SITE` e `HEALTH_CHECK`. Todas as respostas contêm `task_id`, `type`, `status`, `data`, `leads`, `artifacts`, `errors`, `warnings`, `started_at` e `finished_at`.

Exemplo `SEARCH_LEADS`:

```json
{
  "task_id": "2c6b72b1-68a4-4a51-bf59-68aa96832ad5",
  "type": "SEARCH_LEADS",
  "payload": {
    "city": "Porto Alegre",
    "state": "RS",
    "category": "restaurantes",
    "quantity": 20,
    "filters": {}
  }
}
```

`quantity` aceita 1–100. `filters.has_website` é opcional; resultados sem evidência explícita de site não são tratados como empresas sem site. Filtros desconhecidos são indicados em `warnings`, não aplicados silenciosamente.

Exemplos de execução local (substitua o lead de `OPEN_COMPANY`/`GENERATE_SITE` por um lead observado no AIVIO):

```powershell
python -m scout task SEARCH_LEADS --payload '{"city":"Porto Alegre","state":"RS","category":"restaurantes","quantity":20,"filters":{}}'
python -m scout task OPEN_COMPANY --payload '{"lead":{"company_name":"Restaurante Exemplo","city":"Porto Alegre","state":"RS","company_url":"https://app.aivio.example/company/123"}}'
python -m scout task GENERATE_SITE --payload '{"lead":{"company_name":"Restaurante Exemplo","city":"Porto Alegre","state":"RS","company_url":"https://app.aivio.example/company/123"}}'
```

O comando `HEALTH_CHECK` pode ser executado via `/tasks`; `GET /health` também inclui `browser_connected`, `active_tab` e `aivio_available`. O fechamento do Edge/AIVIO é reportado como indisponibilidade e não derruba a API.

Para executar uma tarefa via HTTP, envie o objeto JSON acima a `http://127.0.0.1:8080/tasks`. Os erros de execução retornam um resultado com `status: "failed"` e descrição em `errors`; uma busca que não alcance a quantidade pedida retorna `partial`.

`SCOUT_AIVIO_GENERATION_TIMEOUT_MS` controla o timeout de espera por um sinal de conclusão (padrão 120000 ms; intervalo permitido de 1000 a 600000). A geração retorna artefatos observados como website ou URL PDF; um caminho local só será informado quando realmente disponível.

Como o AIVIO não fornece neste repositório um contrato estável de DOM/URL, a interação usa rótulos acessíveis e estrutura HTML visível. A geração não foi validada contra uma sessão AIVIO real neste ambiente: se o controle acessível ou o sinal de sucesso não for reconhecido, a tarefa falha explicitamente ou retorna aviso, em vez de inventar um resultado.

## Configuração

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `SCOUT_PROVIDER` | `mock` | Provedor disponível nesta versão. Qualquer outro valor é rejeitado. |
| `SCOUT_HOST` | `127.0.0.1` | A API só aceita o loopback `127.0.0.1`. |
| `SCOUT_PORT` | `8080` | Porta HTTP. |
| `SCOUT_LOG_LEVEL` | `INFO` | Nível de log. |
| `SCOUT_CDP_ENDPOINT` | `http://127.0.0.1:9223` no V2; V1 permanece em `http://127.0.0.1:9222` | Endpoint CDP local da versão correspondente. O V2 aceita somente seu endereço loopback HTTP em `127.0.0.1:9223`; a V1 permanece isolada em `127.0.0.1:9222`. |
| `SCOUT_AIVIO_GENERATION_TIMEOUT_MS` | `120000` | Timeout da espera condicional da geração do site (1000–600000 ms). |

O Browser Controller valida que o endpoint fala com Microsoft Edge e não armazena credenciais, cookies ou tokens. Não configure credenciais na URL do endpoint.

## API

### `POST /scout/search`

Entrada:

```json
{
	"query": "dentistas",
	"city": "Porto Alegre",
	"state": "RS",
	"limit": 20
}
```

`query`, `city` e `state` são obrigatórios; `limit` aceita inteiros de 1 a 100. A resposta contém `results`, `total` e `errors`. Os resultados são ordenados por score decrescente.

### `GET /scout/lead/:id`

Retorna o registro completo que foi armazenado durante a execução atual do processo. `GET /health` verifica a disponibilidade da API.

### `POST /tasks`

Executa uma tarefa V2 estruturada. Os tipos aceitos são `SEARCH_LEADS`, `OPEN_COMPANY`, `GENERATE_SITE` e `HEALTH_CHECK`. A rota é adicional; `/scout/search` e `/scout/lead/:id` permanecem disponíveis com seus contratos atuais.

Exemplo de consulta:

```sh
curl -X POST http://127.0.0.1:8080/scout/search \
	-H 'Content-Type: application/json' \
	-d '{"query":"dentistas","city":"Porto Alegre","state":"RS","limit":20}'
```

## Formato do lead

Cada registro inclui `id`, `companyName`, `category`, `city`, `state`, `phone`, `whatsapp`, `website`, `websiteStatus`, `websiteScore`, `websiteAnalysis`, `instagram`, `address`, `source`, `sources`, `opportunityScore`, `opportunityLevel`, `reasons`, `collectedAt` e `signals`. Valores sem evidência permanecem `null`, `not_verified` ou ausentes em `signals`; fontes acompanham os campos fornecidos pelo provedor. O catálogo atual retorna empresas fictícias, marcadas com `source: "mock://catalog"`.

## Pontuação

Regras centralizadas em `scout/scoring.py`; pontos só são atribuídos para evidências positivas/negativas explícitas. Desconhecido não equivale a falso.

| Evidência | Pontos |
| --- | ---: |
| Site confirmado como não encontrado | +40 |
| Site inacessível | +35 |
| Sinais de site desatualizado | +12 |
| Mobile não compatível | +10 |
| CTA não identificado | +8 |
| WhatsApp não identificado | +4 |
| Empresa ativa, presença pública forte ou reputação positiva | +8, +6 ou +6 |
| Categoria comercial relevante | +5 |

A soma é limitada a 100. Classificação: `low` 0–39, `medium` 40–69, `good` 70–84 e `high` 85–100. Os sinais de atividade, avaliações e presença pública só podem ser marcados quando o provedor trouxer evidência e fonte.

## Testes

```sh
python3 -m unittest discover -s tests -v
```

Os testes unitários simulam o transporte CDP e não exigem Edge ativo. Para verificar a instância Edge V2 real já aberta e autorizada pelo usuário, execute `python3 -m scout browser-status`; o padrão é `http://127.0.0.1:9223`. O resultado deve conter `"connected": true`, `"browser": "Microsoft Edge"`, `tabs` com título/URL e `activeTab`. Não tente autenticar nem abrir sites automaticamente.

O teste de empacotamento executa quando PyInstaller está instalado (`python3 -m pip install -r requirements-build.txt`); no Windows, ele verifica que o artefato `.exe` é gerado.

Os testes mockados da integração AIVIO rodam na mesma suíte. O teste real, que preenche a cidade e clica para pesquisar, é opt-in e requer uma sessão AIVIO já aberta e autenticada manualmente no Edge local:

```powershell
$env:AIVIO_LIVE_TEST = "1"
$env:AIVIO_TEST_CITY = "Porto Alegre"
python -m unittest discover -s tests -p "test_aivio_live.py" -v
```

Acrescente `$env:AIVIO_OPEN_FIRST_COMPANY = "1"` para autorizar explicitamente a abertura do primeiro perfil encontrado.

Para executar uma tarefa V2 real específica, configure `SCOUT_AIVIO_LIVE_TASK` e `SCOUT_AIVIO_LIVE_PAYLOAD` e habilite `SCOUT_AIVIO_LIVE_TEST=1`. Use um lead obtido do AIVIO para `OPEN_COMPANY` e `GENERATE_SITE`; para permitir a geração com efeito externo, configure também `SCOUT_AIVIO_LIVE_ALLOW_GENERATE=1`. O teste continua opt-in e não usa AIVIO real na suíte normal:

```powershell
$env:SCOUT_AIVIO_LIVE_TEST = "1"
$env:SCOUT_AIVIO_LIVE_TASK = "SEARCH_LEADS"
$env:SCOUT_AIVIO_LIVE_PAYLOAD = '{"city":"Porto Alegre","state":"RS","category":"restaurantes","quantity":5,"filters":{}}'
python -m unittest discover -s tests -p "test_aivio_live.py" -v
```

O teste de integração real executa o mesmo fluxo e fica ignorado quando `SCOUT_CDP_ENDPOINT` não está configurado:

```sh
SCOUT_CDP_ENDPOINT=http://127.0.0.1:9223 python3 -m unittest discover -s tests -p 'test_browser_controller_live.py' -v
```

## Limitações

- `/scout/search` continua usando o catálogo sintético para demonstração. A busca real do AIVIO existe somente no executor de tarefas V2 (`/tasks` ou `python -m scout task SEARCH_LEADS`).
- A estrutura DOM, os campos de pesquisa, paginação e os rótulos de geração dependem da interface atual do AIVIO; não foi possível confirmar os seletores em uma sessão real neste ambiente. Execute os testes live opt-in no Edge local antes de depender operacionalmente desses fluxos.
- Não há integração com buscadores, Google Maps, redes sociais ou bases de avaliações.
- A auditoria analisa somente o HTML inicial, respeita `robots.txt`, limita o download a 1 MB e não executa JavaScript. Um site pode bloquear a análise; nesse caso, os dados ficam não verificados ou a análise é marcada como bloqueada.
- Compatibilidade mobile é estimada pela presença de `meta viewport`. Performance é tempo de resposta/download, não um resultado de Lighthouse. Aparência visual permanece não verificada sem navegador.
- Campos não observados, como avaliações, endereço, telefone e Instagram, não são preenchidos por inferência.
- Armazenamento é em memória e se perde ao reiniciar. A API não tem autenticação; mantenha-a em interface confiável e não a exponha publicamente sem controles adicionais.
- Não há dashboard, persistência durável ou coleta de métricas Google.

## Integração futura

Um futuro adaptador pode implementar `SearchProvider` em `scout/discovery.py`, respeitando os termos, limites e políticas da fonte. Ele deve retornar somente fatos com atribuição em `sources`; `CompanyResearcher` rejeita registros sem origem nos campos essenciais e remove valores opcionais/sinais sem fonte. A saída HTTP pode ser consumida futuramente pelo Automaton como dados de pesquisa. Essa integração não está implementada: decisões de contato e qualquer comunicação continuam fora do Scout.