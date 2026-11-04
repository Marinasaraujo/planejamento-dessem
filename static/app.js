"use strict";

// 1. Espera o HTML estar disponível.
document.addEventListener("DOMContentLoaded", () => {

  // Atalho para buscar um elemento pelo seu ID. (arrow function)
  const elemento = (id) => document.getElementById(id);

  // Guarda referências aos elementos que vamos controlar.
  const tela = {
    formulario: elemento("formulario-caso"),

    origens: [
      ...document.querySelectorAll('input[name="origem_deck"]')
    ],

    diretorio: elemento("diretorio-deck"),
    carregar: elemento("botao-carregar-deck"),
    preparar: elemento("botao-preparar-caso"),

    uhe: elemento("usinas-hidreletricas"),
    ute: elemento("usinas-termeletricas"),
    buscaUhe: elemento("busca-uhe"),
    buscaUte: elemento("busca-ute"),
    resumoUhe: elemento("resumo-uhe"),
    resumoUte: elemento("resumo-ute"),
    limparUhe: elemento("limpar-uhe"),
    limparUte: elemento("limpar-ute"),
    submercado: elemento("submercado"),

    fonte: elemento("fonte-afluencias"),
    ano: elemento("ano-afluencias"),

    statusApp: elemento("status-aplicacao"),
    statusDeck: elemento("status-deck"),
    statusSelecao: elemento("status-selecao"),

    tabelaUhe: elemento("corpo-tabela-uhe"),
    tabelaUte: elemento("corpo-tabela-ute"),

    carga: elemento("dados-carga"),
    afluencias: elemento("dados-afluencias"),
    json: elemento("json-caso")
  };

  // 2. Estado da aplicação nesta página.
  const estado = {
    deckId: null,
    ocupado: false,
    catalogos: {
      uhe: [],
      ute: []
    }
  };

  // Identifica se a página foi aberta por HTTP ou diretamente como arquivo.
  const servidorDisponivel = ["http:", "https:"].includes(
    location.protocol
  );

  // Retorna "padrao" ou "diretorio".
  const origem = () => {
    return tela.origens.find((radio) => radio.checked).value;
  };

  // Retorna os códigos selecionados em um select múltiplo.
  const selecionados = (select) => {
    return [...select.selectedOptions].map((opcao) => opcao.value);
  };

  // Compara texto sem diferenciar maiúsculas, minúsculas ou acentos.
  const normalizarBusca = (valor) => String(valor ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-BR")
    .trim();

  function configurarBusca(select, campo, resumo, botao) {
    const quantidade = estado.catalogos[campo].length;
    const escolhidos = new Set(selecionados(select));
    const termo = normalizarBusca(select.dataset.termoBusca);
    const catalogo = estado.catalogos[campo];
    const filtrados = catalogo.filter((item) => {
      const texto = normalizarBusca(`${item.nome} ${item.codigo}`);
      return !termo || texto.includes(termo);
    });
    const visiveis = new Map(filtrados.map((item) => [String(item.codigo), item]));

    // Mantém no select o que foi escolhido mesmo quando saiu do filtro.
    for (const item of catalogo) {
      const codigo = String(item.codigo);
      if (escolhidos.has(codigo) && !visiveis.has(codigo)) {
        visiveis.set(codigo, item);
      }
    }

    select.replaceChildren();
    for (const item of visiveis.values()) {
      const codigo = String(item.codigo);
      const foraDoFiltro = termo && !filtrados.some(
        (filtrado) => String(filtrado.codigo) === codigo
      );
      const rotulo = foraDoFiltro
        ? `${item.nome} — ${item.codigo} (selecionada; fora do filtro)`
        : `${item.nome} — ${item.codigo}`;
      const opcao = new Option(rotulo, codigo, false, escolhidos.has(codigo));
      select.add(opcao);
    }

    const selecionadosAgora = selecionados(select).length;
    resumo.textContent = termo
      ? `${filtrados.length} de ${quantidade} usina(s) exibida(s). ` +
        `${selecionadosAgora} selecionada(s).`
      : `${quantidade} usina(s) disponível(is). ` +
        `${selecionadosAgora} selecionada(s).`;
    botao.disabled = estado.ocupado || estado.deckId === null || selecionadosAgora === 0;
  }

  function aplicarBusca(tipo) {
    const select = tipo === "uhe" ? tela.uhe : tela.ute;
    const campo = tipo === "uhe" ? "uhe" : "ute";
    const busca = tipo === "uhe" ? tela.buscaUhe : tela.buscaUte;
    const resumo = tipo === "uhe" ? tela.resumoUhe : tela.resumoUte;
    const botao = tipo === "uhe" ? tela.limparUhe : tela.limparUte;
    select.dataset.termoBusca = busca.value;
    configurarBusca(select, campo, resumo, botao);
  }

  function limparSelecao(tipo) {
    const select = tipo === "uhe" ? tela.uhe : tela.ute;
    for (const opcao of select.options) opcao.selected = false;
    aplicarBusca(tipo);
    limparResultados();
  }

  // Habilita os campos conforme a etapa atual.
  function atualizarControles() {
    const semDeck = estado.deckId === null;

    tela.origens.forEach((radio) => {
      radio.disabled = estado.ocupado;
    });

    // Mostra o campo de diretório somente quando necessário.
    tela.diretorio.parentElement.hidden = origem() !== "diretorio";

    tela.diretorio.disabled =
      estado.ocupado || origem() !== "diretorio";

    tela.carregar.disabled =
      estado.ocupado || !servidorDisponivel;

    for (const campo of [
      tela.uhe,
      tela.ute,
      tela.submercado,
      tela.fonte
    ]) {
      campo.disabled = estado.ocupado || semDeck;
    }

    for (const campo of [tela.buscaUhe, tela.buscaUte]) {
      campo.disabled = estado.ocupado || semDeck;
    }

    for (const botao of [tela.limparUhe, tela.limparUte]) {
      botao.disabled = estado.ocupado || semDeck ||
        selecionados(botao === tela.limparUhe ? tela.uhe : tela.ute).length === 0;
    }

    tela.ano.disabled =
      estado.ocupado ||
      semDeck ||
      tela.fonte.value !== "ano_historico";

    tela.preparar.disabled = estado.ocupado || semDeck;

    tela.formulario.setAttribute(
      "aria-busy",
      String(estado.ocupado)
    );
  }

  // 3. Preenche os seletores com os dados recebidos.
  function preencherSelect(
    select,
    itens,
    mensagem,
    varios = false
  ) {
    select.replaceChildren();

    const inicial = new Option(
      mensagem,
      "",
      !varios,
      !varios
    );

    inicial.disabled = true;
    select.add(inicial);

    for (const item of itens) {
      const opcao = new Option(
        `${item.nome} — ${item.codigo}`,
        String(item.codigo)
      );

      select.add(opcao);
    }
  }

  // Libera as opções históricas conforme a base disponível no Python.
  function configurarHistorico(historico = {}) {
    const anos = historico.anos ?? [];

    tela.ano.replaceChildren(
      new Option("Selecione um ano", "", true, true)
    );

    for (const ano of anos) {
      tela.ano.add(
        new Option(String(ano), String(ano))
      );
    }

    for (const opcao of tela.fonte.options) {
      if (opcao.value === "ano_historico") {
        opcao.disabled = anos.length === 0;

        opcao.textContent = opcao.disabled
          ? "Ano histórico — requer base histórica"
          : "Ano histórico";

      } else if (opcao.value === "media_historica") {
        opcao.disabled = historico.permite_media !== true;

        opcao.textContent = opcao.disabled
          ? "Médias históricas — requer base histórica"
          : "Médias históricas";

      } else {
        opcao.disabled = false;
      }
    }

    tela.fonte.value = "deck";
  }

  // 4. Funções para apresentar os dados.
  function formatar(valor) {
    if (valor === null || valor === undefined) {
      return "—";
    }

    if (typeof valor === "number") {
      return Number.isFinite(valor)
        ? valor.toLocaleString("pt-BR", {
            maximumFractionDigits: 3
          })
        : "—";
    }

    return String(valor);
  }

  function preencherTabela(corpo, linhas, campos) {
    corpo.replaceChildren();

    if (linhas.length === 0) {
      const linha = corpo.insertRow();
      const celula = linha.insertCell();

      celula.colSpan = campos.length;
      celula.textContent =
        "Nenhum dado disponível para esta seleção.";

      return;
    }

    for (const registro of linhas) {
      const linha = corpo.insertRow();

      for (const campo of campos) {
        linha.insertCell().textContent = formatar(
          registro[campo]
        );
      }
    }
  }

  function mostrarSerie(destino, linhas, colunas, descricao) {
    destino.replaceChildren();

    if (
      linhas.length > 0 &&
      typeof linhas[0] !== "object"
    ) {
      linhas = linhas.map((valor) => ({ valor }));
      colunas = [
        { campo: "valor", titulo: "Carga (MW)" }
      ];
    }

    const tabela = document.createElement("table");

    const legenda = tabela.createCaption();
    legenda.textContent = descricao;

    const cabecalho = tabela.createTHead().insertRow();

    for (const coluna of colunas) {
      const th = document.createElement("th");

      th.scope = "col";
      th.textContent = coluna.titulo;

      cabecalho.append(th);
    }

    preencherTabela(
      tabela.createTBody(),
      linhas,
      colunas.map((coluna) => coluna.campo)
    );

    destino.append(tabela);
  }

  function limparResultados() {
    preencherTabela(tela.tabelaUhe, [], [
      "codigo",
      "nome",
      "volume_min_hm3",
      "volume_max_hm3",
      "volume_inicial_hm3"
    ]);

    preencherTabela(tela.tabelaUte, [], [
      "codigo",
      "nome",
      "unidade"
    ]);

    tela.carga.textContent =
      "Aguardando consulta da carga.";

    tela.afluencias.textContent =
      "Aguardando consulta das afluências.";

    tela.json.textContent =
      "Nenhum dado preparado.";

    tela.statusSelecao.textContent =
      "Seleção atual ainda não preparada.";
  }

  // Descarta o caso anterior ao trocar a configuração do deck.
  function invalidarDeck() {
    estado.deckId = null;
    estado.catalogos.uhe = [];
    estado.catalogos.ute = [];
    tela.buscaUhe.value = "";
    tela.buscaUte.value = "";

    preencherSelect(
      tela.uhe,
      [],
      "Aguardando leitura do deck",
      true
    );

    preencherSelect(
      tela.ute,
      [],
      "Aguardando leitura do deck",
      true
    );

    preencherSelect(
      tela.submercado,
      [],
      "Aguardando leitura do deck"
    );

    configurarHistorico();
    limparResultados();

    tela.statusDeck.textContent =
      "Nenhum deck carregado para esta configuração.";

    atualizarControles();
  }

  // 5. Envia um objeto ao Python e recebe outro objeto.
  async function enviarJson(url, dados) {
    if (!servidorDisponivel) {
      throw new Error(
        "Abra a página pelo servidor Python para consultar o deck."
      );
    }

    let resposta;

    try {
      resposta = await fetch(url, {
        method: "POST",

        headers: {
          "Content-Type": "application/json"
        },

        // Converte o objeto JavaScript em texto JSON.
        body: JSON.stringify(dados)
      });

    } catch {
      throw new Error(
        "Não foi possível conectar ao servidor Python."
      );
    }

    const tipoConteudo = resposta.headers.get("content-type");

    if (!tipoConteudo?.includes("application/json")) {
      throw new Error(
        `O servidor não retornou JSON (HTTP ${resposta.status}). ` +
        "Verifique se a rota Python foi implementada."
      );
    }

    // Converte o JSON da resposta em um objeto JavaScript.
    const resultado = await resposta.json();

    if (!resposta.ok) {
      throw new Error(
        resultado.erro || `Erro HTTP ${resposta.status}.`
      );
    }

    return resultado;
  }

  // 6. Carrega o deck e recebe os catálogos.
  async function carregarDeck() {
    if (estado.ocupado) return;

    if (
      origem() === "diretorio" &&
      !tela.diretorio.value.trim()
    ) {
      tela.statusDeck.textContent =
        "Informe o diretório do deck.";

      tela.diretorio.focus();
      return;
    }

    invalidarDeck();

    estado.ocupado = true;
    atualizarControles();

    tela.statusDeck.textContent = "Carregando o deck…";

    try {
      const dados = await enviarJson("/api/deck/carregar", {
        origem_deck: origem(),

        diretorio_deck: origem() === "diretorio"
          ? tela.diretorio.value.trim()
          : null
      });

      if (
        typeof dados.deck_id !== "string" ||
        !dados.deck_id ||
        !dados.nome ||
        !dados.data_referencia ||
        !Array.isArray(dados.uhe) ||
        !Array.isArray(dados.ute) ||
        !Array.isArray(dados.submercados)
      ) {
        throw new Error(
          "A resposta do carregamento está incompleta."
        );
      }

      preencherSelect(
        tela.uhe,
        dados.uhe,
        "Selecione as UHEs",
        true
      );

      preencherSelect(
        tela.ute,
        dados.ute,
        "Selecione as UTEs",
        true
      );

      estado.catalogos.uhe = dados.uhe;
      estado.catalogos.ute = dados.ute;
      aplicarBusca("uhe");
      aplicarBusca("ute");

      preencherSelect(
        tela.submercado,
        dados.submercados,
        "Selecione o submercado"
      );

      configurarHistorico(dados.historico);

      estado.deckId = dados.deck_id;

      const avisos = dados.avisos?.length
        ? ` Avisos: ${dados.avisos.join("; ")}`
        : "";

      tela.statusDeck.textContent =
        `Deck carregado: ${dados.nome}. ` +
        `Referência: ${dados.data_referencia}.` +
        avisos;

    } catch (erro) {
      invalidarDeck();
      tela.statusDeck.textContent = erro.message;

    } finally {
      estado.ocupado = false;
      atualizarControles();
    }
  }

  // 7. Envia as escolhas e recebe o caso preparado.
  async function prepararCaso() {
    if (estado.ocupado || estado.deckId === null) {
      return;
    }

    limparResultados();

    if (!tela.submercado.value) {
      tela.statusSelecao.textContent =
        "Selecione o submercado da carga.";

      return;
    }

    if (
      tela.fonte.value === "ano_historico" &&
      !tela.ano.value
    ) {
      tela.statusSelecao.textContent =
        "Selecione o ano das afluências.";

      return;
    }

    estado.ocupado = true;
    atualizarControles();

    tela.statusSelecao.textContent =
      "Consultando e preparando os dados…";

    try {
      const caso = await enviarJson("/api/caso/preparar", {
        deck_id: estado.deckId,

        codigos_uhe: selecionados(tela.uhe),
        codigos_ute: selecionados(tela.ute),

        codigo_submercado: tela.submercado.value,
        fonte_afluencias: tela.fonte.value,

        ano_afluencias: tela.fonte.value === "ano_historico"
          ? Number(tela.ano.value)
          : null
      });

      for (const campo of [
        "uhe",
        "ute",
        "carga",
        "afluencias"
      ]) {
        if (!Array.isArray(caso[campo])) {
          throw new Error(
            `Resposta inválida: falta a lista ${campo}.`
          );
        }
      }

      preencherTabela(tela.tabelaUhe, caso.uhe, [
        "codigo",
        "nome",
        "volume_min_hm3",
        "volume_max_hm3",
        "volume_inicial_hm3"
      ]);

      preencherTabela(tela.tabelaUte, caso.ute, [
        "codigo",
        "nome",
        "unidade"
      ]);

      mostrarSerie(
        tela.carga,
        caso.carga,
        [
          { campo: "inicio", titulo: "Início" },
          { campo: "fim", titulo: "Fim" },
          { campo: "duracao_h", titulo: "Duração (h)" },
          { campo: "valor_mw", titulo: "Carga (MW)" }
        ],
        "Carga do submercado selecionado"
      );

      mostrarSerie(
        tela.afluencias,
        caso.afluencias,
        [
          { campo: "codigo_usina", titulo: "Código da UHE" },
          { campo: "inicio", titulo: "Início" },
          { campo: "fim", titulo: "Fim" },
          { campo: "vazao_m3s", titulo: "Afluência (m³/s)" },
          { campo: "tipo", titulo: "Tipo de vazão" }
        ],
        "Afluências das UHEs selecionadas"
      );

      // Mostra o objeto completo, com indentação de dois espaços.
      tela.json.textContent = JSON.stringify(caso, null, 2);

      const pendencias = caso.pendencias?.length
        ? ` Pendências: ${caso.pendencias.join("; ")}`
        : "";

      tela.statusSelecao.textContent =
        "Dados recebidos do Python. " +
        "Nenhuma otimização executada." +
        pendencias;

    } catch (erro) {
      limparResultados();
      tela.statusSelecao.textContent = erro.message;

    } finally {
      estado.ocupado = false;
      atualizarControles();
    }
  }

  // 8. Associa os eventos da página às funções.
  tela.formulario.addEventListener("submit", (evento) => {
    evento.preventDefault();
  });

  tela.origens.forEach((radio) => {
    radio.addEventListener("change", invalidarDeck);
  });

  tela.diretorio.addEventListener("input", invalidarDeck);

  for (const campo of [
    tela.uhe,
    tela.ute,
    tela.submercado,
    tela.fonte,
    tela.ano
  ]) {
    campo.addEventListener("change", () => {
      limparResultados();
      atualizarControles();
    });
  }

  tela.carregar.addEventListener("click", carregarDeck);
  tela.preparar.addEventListener("click", prepararCaso);

  tela.buscaUhe.addEventListener("input", () => aplicarBusca("uhe"));
  tela.buscaUte.addEventListener("input", () => aplicarBusca("ute"));
  tela.limparUhe.addEventListener("click", () => limparSelecao("uhe"));
  tela.limparUte.addEventListener("click", () => limparSelecao("ute"));
  tela.uhe.addEventListener("change", () => aplicarBusca("uhe"));
  tela.ute.addEventListener("change", () => aplicarBusca("ute"));

  // Configuração inicial da interface.
  invalidarDeck();

  tela.statusApp.textContent = servidorDisponivel
    ? "Interface pronta. O carregamento depende das rotas do servidor Python."
    : "Visualização local: as interações dos campos funcionam. " +
      "Para ler o deck, será necessário iniciar o servidor Python " +
      "e abrir a página pelo endereço dele.";
});
