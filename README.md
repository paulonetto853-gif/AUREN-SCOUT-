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

O resultado é `dist\AurenScout.exe`. Executar:

```powershell
.\dist\AurenScout.exe
```

O processo mantém a API local ativa até Ctrl+C. Verificar em outro PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

A resposta deve indicar `status: ok` e `operational: true`. Para desenvolvimento no Windows, use `.\scripts\run-scout.ps1`.

O Browser Controller é executado sob demanda, sem abrir ou autenticar o Edge. O usuário deve iniciar o Microsoft Edge com depuração remota habilitada, entrar manualmente no site desejado e manter o navegador aberto. Depois, consulte o estado via CDP:

```sh
SCOUT_CDP_ENDPOINT=http://127.0.0.1:9222 python3 -m scout browser-status
```

Para solicitar explicitamente uma navegação na aba ativa:

```sh
SCOUT_CDP_ENDPOINT=http://127.0.0.1:9222 python3 -m scout browser-navigate https://example.com/
```

O comando de status retorna conexão, navegador, abas com título/URL e aba ativa em JSON. Navegação aceita somente URLs HTTP(S) sem credenciais; nenhuma URL é aberta automaticamente. Não exponha o endpoint CDP a redes não confiáveis.

## Execução local no Windows

O comando que controla o Edge precisa rodar no mesmo Windows do navegador. Um terminal do GitHub Codespaces, WSL ou outro container não compartilha o `127.0.0.1` do Windows; execute estes passos em um PowerShell local, dentro de uma cópia do repositório clonada no Windows.

1. Instale Python para Windows conforme a política da sua máquina e abra PowerShell na pasta do repositório.
2. Instale as dependências do projeto:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Inicie o Edge autorizado com CDP local:

   ```powershell
   .\scripts\start-edge-cdp.ps1
   ```

   O script encontra o executável do Edge pelo registro do Windows; não presume um caminho de instalação. Ele cria e usa um perfil dedicado em `%LOCALAPPDATA%\AurenScout\EdgeProfile` e limita o CDP a `127.0.0.1:9222`. Edge recente exige um diretório de dados separado para habilitar depuração remota; esse perfil novo não é uma cópia do perfil pessoal. Não aponte o script para o perfil pessoal e não copie cookies ou credenciais. A primeira vez, faça o login no AIVIO manualmente nesse perfil e mantenha o Edge aberto.

4. No mesmo PowerShell local, configure o endpoint e consulte o estado:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:9222/json/version | ConvertTo-Json -Depth 4
   $env:SCOUT_CDP_ENDPOINT = "http://127.0.0.1:9222"
   python -m scout browser-status
   ```

   A primeira linha confirma que o endpoint CDP local responde. A resposta do Scout deve ser JSON com `"connected": true`, `"browser": "Microsoft Edge"`, `tabs` (título e URL de cada aba) e `activeTab`. Se o projeto estiver em Codespaces, faça um clone local no Windows e execute `py -m pip install` e o comando de status a partir desse clone — não execute o cliente CDP no terminal remoto.

O script não cria túneis, não altera firewall e não autentica no AIVIO. O endpoint CDP dá controle elevado sobre a instância do navegador: mantenha-o vinculado ao loopback, não encaminhe a porta e não o exponha à rede/internet. Se a porta estiver ocupada ou o Edge não puder ser localizado pelo registro, o script para com erro em vez de escolher outro executável ou iniciar outro navegador.

## Configuração

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `SCOUT_PROVIDER` | `mock` | Provedor disponível nesta versão. Qualquer outro valor é rejeitado. |
| `SCOUT_HOST` | `127.0.0.1` | A API só aceita o loopback `127.0.0.1`. |
| `SCOUT_PORT` | `8080` | Porta HTTP. |
| `SCOUT_LOG_LEVEL` | `INFO` | Nível de log. |
| `SCOUT_CDP_ENDPOINT` | (não configurado) | URL HTTP(S) do endpoint CDP do Edge que o usuário disponibilizou. |

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

Os testes unitários simulam o transporte CDP e não exigem Edge ativo. Para verificar uma instância Edge real já aberta e autorizada pelo usuário, execute `SCOUT_CDP_ENDPOINT=http://127.0.0.1:9222 python3 -m scout browser-status`; o resultado deve conter `"connected": true`, `"browser": "Microsoft Edge"`, `tabs` com título/URL e `activeTab`. Não tente autenticar nem abrir sites automaticamente.

O teste de empacotamento executa quando PyInstaller está instalado (`python3 -m pip install -r requirements-build.txt`); no Windows, ele verifica que o artefato `.exe` é gerado.

O teste de integração real executa o mesmo fluxo e fica ignorado quando `SCOUT_CDP_ENDPOINT` não está configurado:

```sh
SCOUT_CDP_ENDPOINT=http://127.0.0.1:9222 python3 -m unittest discover -s tests -p 'test_browser_controller_live.py' -v
```

## Limitações

- A busca disponível é um catálogo sintético para demonstração, não uma pesquisa na internet. Não produz leads reais nem afirma que empresas demonstrativas existem.
- Não há integração com buscadores, Google Maps, redes sociais ou bases de avaliações.
- A auditoria analisa somente o HTML inicial, respeita `robots.txt`, limita o download a 1 MB e não executa JavaScript. Um site pode bloquear a análise; nesse caso, os dados ficam não verificados ou a análise é marcada como bloqueada.
- Compatibilidade mobile é estimada pela presença de `meta viewport`. Performance é tempo de resposta/download, não um resultado de Lighthouse. Aparência visual permanece não verificada sem navegador.
- Campos não observados, como avaliações, endereço, telefone e Instagram, não são preenchidos por inferência.
- Armazenamento é em memória e se perde ao reiniciar. A API não tem autenticação; mantenha-a em interface confiável e não a exponha publicamente sem controles adicionais.
- Não há dashboard, pesquisa externa, persistência durável ou coleta de métricas Google.

## Integração futura

Um futuro adaptador pode implementar `SearchProvider` em `scout/discovery.py`, respeitando os termos, limites e políticas da fonte. Ele deve retornar somente fatos com atribuição em `sources`; `CompanyResearcher` rejeita registros sem origem nos campos essenciais e remove valores opcionais/sinais sem fonte. A saída HTTP pode ser consumida futuramente pelo Automaton como dados de pesquisa. Essa integração não está implementada: decisões de contato e qualquer comunicação continuam fora do Scout.