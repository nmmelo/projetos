# estatisticas_github

Página de perfil do GitHub (**Profile README**) com apresentação, *tech stack* em badges, estatísticas
dinâmicas e contactos. Inspirada no perfil de [OsodracPT](https://github.com/OsodracPT).

As estatísticas são geradas pelo [github-stats-extended](https://github.com/stats-organization/github-stats-extended),
um fork do `github-readme-stats` com mais opções e os mesmos parâmetros.

| Ficheiro | Conteúdo |
|---|---|
| [`exemplo/README.md`](exemplo/README.md) | README pronto a usar no perfil `nmmelo` |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem a este guia |

## Como funciona o Profile README

O GitHub mostra no topo do perfil o `README.md` de um repositório **público** com o **mesmo nome que o username**.

| Regra | Detalhe |
|---|---|
| Nome do repositório | Exatamente igual ao username: `nmmelo/nmmelo` |
| Visibilidade | **Public** |
| Localização do ficheiro | `README.md` na raiz da branch principal (`main`). Numa subpasta não aparece |
| Outro nome | Não funciona. Fica um repositório normal |

## Passos

| Passo | Ação |
|---|---|
| 1 | GitHub → **New repository** → nome `nmmelo` (aparece a mensagem *special repository*) |
| 2 | Marcar **Public** e **Add a README file** → **Create repository** |
| 3 | Copiar o conteúdo de [`exemplo/README.md`](exemplo/README.md) para o `README.md` e fazer commit |
| 4 | Trocar `O_TEU_PERFIL` pelo endereço do LinkedIn (ou apagar a linha) |
| 5 | No perfil → **Customize your pins** → fixar até 6 repositórios (ex. `claude`) |
| 6 | **Settings → Public profile**: foto, nome, bio e localização |

### Se o repositório `nmmelo` já existir

| Situação | O que fazer |
|---|---|
| Vazio ou pode ser usado | Torná-lo **Public** e colocar o `README.md` na raiz |
| Tem outro projeto a manter | **Settings → General → Repository name** → mudar para ex. `nmmelo-old` e criar um novo `nmmelo` |

Após mudar o nome, o GitHub redireciona os links antigos. Nos clones locais:

```bash
git remote set-url origin https://github.com/nmmelo/nmmelo-old.git
```

## github-stats-extended

Instância pública: `https://github-stats-extended.vercel.app`
Assistente visual para gerar os URLs: <https://github-stats-extended.vercel.app/frontend>

### Cartões usados

| Cartão | URL |
|---|---|
| Estatísticas | `https://github-stats-extended.vercel.app/api?username=nmmelo&show_icons=true&theme=algolia&include_all_commits=true` |
| Linguagens | `https://github-stats-extended.vercel.app/api/top-langs/?username=nmmelo&layout=compact&langs_count=8&theme=algolia` |

Para testar, abrir os URLs no browser: tem de aparecer a imagem do cartão.

### Parâmetros principais

| Parâmetro | Exemplo | Efeito |
|---|---|---|
| `username` | `nmmelo` | Utilizador GitHub (obrigatório) |
| `theme` | `algolia`, `radical`, `calm`, `dark` | Tema de cores |
| `show_icons` | `true` | Ícones ao lado de cada estatística |
| `include_all_commits` | `true` | Conta todos os commits e não só os do ano |
| `rank_icon` | `github` | Ícone do rank |
| `custom_title` | `As%20minhas%20estat%C3%ADsticas` | Título do cartão (URL-encoded) |
| `disable_animations` | `true` | Desliga as animações |
| `show` | `reviews,prs_merged` | Estatísticas adicionais |
| `langs_count` | `8` | N.º de linguagens no cartão `top-langs` |
| `layout` | `compact` | Layout do cartão `top-langs` |

### Código no README

```html
<p align="left">
  <a href="https://github.com/nmmelo">
    <img height="170" src="https://github-stats-extended.vercel.app/api?username=nmmelo&show_icons=true&theme=algolia&include_all_commits=true" />
    <img height="170" src="https://github-stats-extended.vercel.app/api/top-langs/?username=nmmelo&layout=compact&langs_count=8&theme=algolia" />
  </a>
</p>
```

## Limitações e problemas

| Problema | Causa / solução |
|---|---|
| Cartão não aparece com `github-readme-stats.vercel.app` | Instância pública original sobrecarregada (limite de 5000 pedidos/hora da API GitHub) e por vezes pausada (`503 DEPLOYMENT_PAUSED`). Usar o `github-stats-extended` |
| `count_private=true` | Não documentado no `github-stats-extended`; retirado. Repositórios privados só contam numa instância própria com token |
| `layout=compact` não funciona | Apagar o parâmetro |
| `github-stats-extended` começar a falhar | Também é uma instância pública partilhada. Alternativa: alojar uma cópia própria no Vercel (ver abaixo) |

### Instância própria no Vercel (alternativa)

| Passo | Ação |
|---|---|
| 1 | Conta em <https://vercel.com/signup> (entrar com o GitHub, plano Hobby) |
| 2 | **Fork** de <https://github.com/anuraghazra/github-readme-stats> |
| 3 | GitHub → **Settings → Developer settings → Personal access tokens (classic)** → scope `public_repo` (ou `repo` para incluir privados) |
| 4 | Vercel → **Add New → Project** → importar o fork |
| 5 | **Environment Variables** → `PAT_1` = token → **Deploy** |
| 6 | Usar o URL fixo de **Domains** (não o URL longo de cada deploy) no lugar de `github-stats-extended.vercel.app` |

O token é secreto: fica só no Vercel, nunca no README.
