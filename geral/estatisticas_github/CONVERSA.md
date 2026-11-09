# Conversa: estatisticas_github

Chat com o Claude, 9 de novembro de 2026.

---

## 1. Página inicial do GitHub

**Pergunta:**
> como posso colocar a minha pagina inicial de github para outras pessoas ver como este

**Resposta:** duas opções — **Profile README** (repositório com o nome do username, ex. `nmmelo/nmmelo`)
ou **GitHub Pages** (`nmmelo.github.io`). Passos para criar o Profile README e tornar o perfil visível.

## 2. Exemplo

**Pergunta:**
> [OsodracPT (Pedro Cardoso)](https://github.com/OsodracPT)

**Resposta:** analisado o perfil: saudação, tech stack em badges shields.io, estatísticas do
github-readme-stats (tema `algolia`) e links de contacto. Gerado um `README.md` no mesmo estilo,
adaptado ao perfil de DBA (Oracle, PostgreSQL, MySQL, MariaDB, SQL Server, Ansible, Docker, Linux, Shell).

## 3. Repositório já existente

**Pergunta:**
> e se já tiver um repositorio nmmelo, posso criar com outro nome

**Resposta:** não — o nome tem de ser igual ao username. Usar o existente (público, README na raiz) ou
mudar-lhe o nome (ex. `nmmelo-old`) e criar um novo `nmmelo`.

## 4. Estatísticas não aparecem

**Pergunta:**
> isto não dá, https://github-readme-stats.vercel.app/api?username=nmmelo&...

**Resposta:** a instância pública do github-readme-stats é partilhada, atinge os limites da API GitHub
e já esteve pausada (`503 DEPLOYMENT_PAUSED`). Solução: instância própria no Vercel com token em `PAT_1`.

## 5. github-stats-extended

**Pergunta:**
> e usar este, stats-organization/github-stats-extended

**Resposta:** sim — fork com os mesmos parâmetros; basta trocar o domínio para
`github-stats-extended.vercel.app`. Retirado `count_private` (não documentado).

## 6. Guardar no repositório

**Pergunta:**
> podes pegar nisto e fazer no meu repositorio projetos/geral/estatisticas_github e colocar lá o exemplo e o que é necessário fazer para usar o github-stats-extended

**Resposta:** criada a pasta `geral/estatisticas_github` no repositório `projetos` (antigo `claude`) com o guia
(`README.md`), o exemplo (`exemplo/README.md`) e esta conversa; atualizados os README de `geral` e da raiz.
