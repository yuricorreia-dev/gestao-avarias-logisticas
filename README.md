# Gestão de Avarias

Aplicação web local para registrar ocorrências, analisar destinos e acompanhar indicadores operacionais por setor. O backend usa Python 3.10+ e SQLite; a interface inclui módulos React/TypeScript compilados com Vite.

## Executar

```sh
npm install
npm run build
python3 server.py
```

Acesse http://127.0.0.1:8000. Na primeira execução, o sistema cria `avarias.db`, uma chave de sessão local e registros de demonstração. Para iniciar com outro banco, defina `AVARIAS_DB=/caminho/arquivo.db`. `HOST` e `PORT` também podem ser definidos por variável de ambiente.

## Usuários de demonstração

| Usuário | Senha | Perfil |
| --- | --- | --- |
| `admin` | `admin123` | Administrador e configuração |
| `analista` | `analista123` | Registro e revisão operacional |
| `aprovador` | `aprovador123` | Registro, revisão e aprovação |

As senhas são armazenadas com scrypt. Os perfis autorizados a aprovar são configuráveis nos parâmetros. A decisão financeira só é contabilizada depois de aprovada. Recuperação e valor recolhido são independentes e só entram nos indicadores quando confirmados explicitamente.

## Módulos

- **Gestão por Setores**: cartões para Fracionado, Caixa, Organização e demais setores presentes nos dados; painel individual, gráficos clicáveis, comparação e ranking por meta, valor líquido, recuperação, ocorrências únicas ou sinalização.
- **Metas setoriais**: limites mensal e diário por setor/período; administradores podem cadastrar setores e ajustar metas sem valores fixos no código.
- **Lançamentos**: cadastro em quatro etapas, rascunhos, busca pelo catálogo, total calculado, edição, observações, fotografias e cancelamento auditado.
- **Usuários e permissões**: criação/desativação de usuários, alteração de perfil e configuração dos aprovadores.
- **Telas por função**: Proprietário/Admin, Gestor, Supervisor, Analista, Operador e Visualizador entram em painéis próprios; as ações disponíveis são derivadas das permissões efetivas da sessão.
- **Matriz funcional**: Proprietário/Admin administram; Gestor acompanha metas e decisões; Supervisor revisa setores; Analista trabalha a Central de Análise; Operador registra; Visualizador consulta sem alteração.
- **Multiempresa**: corporações novas têm banco operacional isolado, cadastro sem dados fictícios, confirmação de e-mail, convites expirados/de uso único, seleção de organização e sessões revogáveis.
- **Parâmetros financeiros**: seleção das classificações que compõem a avaria bruta, além dos critérios de desconto já adotados pela empresa.

## Fluxo

1. Registre o lançamento original e sua classificação inicial.
2. A Central de análise permite revisar a classificação, informar decisão proposta, justificativa e evidência.
3. Um perfil aprovador confirma a decisão de incluir ou excluir do desconto.
4. Quantidades podem ser divididas entre destinos, com saldos, recuperação e recolhimento proporcionais preservados.
5. Dashboards e relatórios consultam os dados persistidos; os gráficos e indicadores abrem os lançamentos de origem. A exportação gera CSV compatível com planilhas.

O motivo, a classificação, a sinalização, o setor e a decisão financeira são campos independentes. Alterações materiais exigem justificativa; reclassificações exigem justificativa e evidência. Cancelamentos permanecem consultáveis no histórico, mas deixam de compor indicadores financeiros. Os parâmetros de desconto devem refletir a regra definida pela própria empresa; o sistema não infere significado jurídico ou operacional.

## Limites de uso

Esta versão é um ponto de partida funcional para execução local e avaliação. Antes de disponibilizar em rede ou usar em produção, substitua as credenciais de demonstração, configure HTTPS e gestão corporativa de identidade, defina política de backup/retensão do SQLite e revise os requisitos de auditoria e acesso da empresa. O servidor padrão escuta somente em `127.0.0.1`.
