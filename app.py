# 	Receber requisições do JavaScript, chamar as funções Python e devolver respostas JSON

"""Dashboard local para carregar um deck e conferir sua seleção."""

from pathlib import Path
import math
from secrets import token_hex
from uuid import uuid4

from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    session,
) # framework para construir a api

from werkzeug.exceptions import HTTPException

from leitor_dessem import carregar_deck, listar_catalogos, listar_demandas
from preparacao import montar_sistema


# 1. Configuração da aplicação.
BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

app.secret_key = token_hex(32)
app.json.ensure_ascii = False

# Considera que a pasta do deck está ao lado deste app.py.
app.config["DECK_PADRAO"] = (
    BASE_DIR / "DS_CCEE_102026_SEMREDE_RV4D24"
)

# Memória do processo: o deck é lido uma vez e reutilizado nas consultas.
# Cada sessão do navegador acessa apenas o seu deck atual.
decks = {}


# 2. Funções auxiliares.
def receber_json():
    dados = request.get_json()

    if not isinstance(dados, dict):
        abort(
            400,
            description="Envie um objeto JSON.",
        )

    return dados


def numero_opcional_nao_negativo(dados, campo, rotulo):
    """Lê um número opcional do JSON; vazio significa usar o default."""
    valor = dados.get(campo)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None

    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError) as erro:
        abort(400, description=f"{rotulo} deve ser numérico.")

    if not math.isfinite(numero) or numero < 0:
        abort(400, description=f"{rotulo} deve ser finito e não negativo.")

    return numero


def selecionar(catalogo, codigos, rotulo):
    """Busca os códigos recebidos nos dados guardados no Python."""

    if (
        not isinstance(codigos, list)
        or any(type(c) not in (str, int) for c in codigos)
    ):
        abort(
            400,
            description=f"Envie uma lista de códigos para {rotulo}.",
        )

    por_codigo = {
        str(item["codigo"]): item
        for item in catalogo
    }

    resultado = []
    vistos = set()

    for valor in codigos:
        codigo = str(valor).strip()

        if codigo not in por_codigo:
            abort(
                400,
                description=(
                    f"Código não encontrado em {rotulo}: {codigo}"
                ),
            )

        if codigo not in vistos:
            resultado.append(dict(por_codigo[codigo]))
            vistos.add(codigo)

    return resultado


# 3. Rota que abre a página.
@app.get("/")
def index():
    return render_template("index.html")


# 4. Rota chamada pelo botão "Carregar deck".
@app.post("/api/deck/carregar")
def api_carregar_deck():
    dados = receber_json()

    origem = dados.get("origem_deck")

    if origem == "padrao":
        pasta = Path(app.config["DECK_PADRAO"])

    elif origem == "diretorio":
        caminho = dados.get("diretorio_deck")

        if not isinstance(caminho, str) or not caminho.strip():
            abort(
                400,
                description="Informe o diretório do deck.",
            )

        pasta = Path(caminho.strip()).expanduser()

        if not pasta.is_absolute():
            pasta = BASE_DIR / pasta

    else:
        abort(
            400,
            description=(
                "origem_deck deve ser 'padrao' ou 'diretorio'."
            ),
        )

    pasta = pasta.resolve()

    try:
        caso = carregar_deck(pasta)

        catalogos = listar_catalogos(
            caso,
            fonte_termicas="termdat",
        )

    except (FileNotFoundError, ValueError) as erro:
        return jsonify(erro=str(erro)), 400

    except Exception:
        app.logger.exception("Falha na leitura do deck")

        return jsonify(
            erro=(
                "Não foi possível ler o deck. "
                "Confira o erro no terminal do Python."
            )
        ), 500

    # Obtém a data efetivamente lida do DADVAZ.
    inicio = getattr(
        getattr(caso, "dadvaz", None),
        "data_hora",
        None,
    )

    try:
        referencia = inicio.strftime("%d/%m/%Y")

    except (AttributeError, ValueError, TypeError):
        referencia = "não identificada"

    # Cria uma identificação para este carregamento.
    deck_id = uuid4().hex

    anterior = session.get("deck_id")

    decks[deck_id] = {
        "caso": caso,
        "catalogos": catalogos,
        "nome": pasta.name,
        "data_referencia": referencia,
    }

    session["deck_id"] = deck_id

    # Substitui o deck anterior desta sessão.
    if anterior:
        decks.pop(anterior, None)

    return jsonify(
        deck_id=deck_id,
        nome=pasta.name,
        data_referencia=referencia,
        **catalogos,

        avisos=[
            "Nesta etapa são consultados os catálogos; "
            "a carga DP será extraída após a seleção do submercado; "
            "afluências ainda não são extraídas."
        ],
    )


# 5. Rota chamada pelo botão "Consultar seleção".
@app.post("/api/caso/preparar")
def api_consultar_selecao():
    """Monta o sistema da seleção usando dados cadastrais e defaults."""
    dados = receber_json()
    deck_id = dados.get("deck_id")

    if (
        not isinstance(deck_id, str)
        or deck_id != session.get("deck_id")
    ):
        abort(
            409,
            description="Carregue o deck novamente nesta sessão.",
        )

    deck = decks.get(deck_id)

    if deck is None:
        abort(
            409,
            description=(
                "O deck não está mais na memória. "
                "Carregue novamente."
            ),
        )

    catalogos = deck["catalogos"]

    uhe = selecionar(
        catalogos["uhe"],
        dados.get("codigos_uhe", []),
        "UHEs",
    )

    ute = selecionar(
        catalogos["ute"],
        dados.get("codigos_ute", []),
        "UTEs",
    )

    submercado = selecionar(
        catalogos["submercados"],
        [dados.get("codigo_submercado")],
        "submercados",
    )[0]

    try:
        multiplicador_carga = float(dados.get("multiplicador_carga", 1))
    except (TypeError, ValueError, OverflowError) as erro:
        abort(400, description="O multiplicador da carga deve ser numérico.")

    if not math.isfinite(multiplicador_carga) or multiplicador_carga < 0:
        abort(400, description="O multiplicador da carga deve ser finito e não negativo.")

    try:
        carga = listar_demandas(
            deck["caso"],
            submercado["codigo"],
        )
        carga = [valor * multiplicador_carga for valor in carga]
    except (ValueError, KeyError, TypeError) as erro:
        return jsonify(
            erro=f"Não foi possível consultar a carga: {erro}"
        ), 400

    parametros_uhe = dados.get("parametros_uhe", [])
    if not isinstance(parametros_uhe, list):
        abort(400, description="Os parâmetros das UHEs devem ser uma lista.")

    codigos_selecionados = {str(item["codigo"]) for item in uhe}
    codigos_parametros = set()
    for item in parametros_uhe:
        if not isinstance(item, dict) or "codigo" not in item:
            abort(400, description="Cada UHE deve informar seu código.")
        codigo = str(item["codigo"])
        if codigo not in codigos_selecionados:
            abort(400, description=f"Parâmetros enviados para UHE não selecionada: {codigo}.")
        if codigo in codigos_parametros:
            abort(400, description=f"Parâmetros repetidos para a UHE: {codigo}.")
        codigos_parametros.add(codigo)
        for campo, rotulo in (
            ("afluencia", "Afluência"),
            ("produtibilidade", "Produtibilidade"),
            ("engolimento", "Engolimento"),
        ):
            numero_opcional_nao_negativo(item, campo, f"{rotulo} da UHE {codigo}")
    if codigos_parametros != codigos_selecionados:
        faltantes = ", ".join(sorted(codigos_selecionados - codigos_parametros))
        abort(400, description=f"Faltam parâmetros para as UHEs selecionadas: {faltantes}.")

    try:
        sistema = montar_sistema(
            uhe,
            ute,
            caso=deck["caso"],
            carga=carga,
            codigo_submercado=submercado["codigo"],
            parametros_uhe=parametros_uhe,
        )
    except (ValueError, KeyError, TypeError) as erro:
        return jsonify(erro=f"Não foi possível montar o sistema: {erro}"), 400



    deck["sistema"] = sistema

    return jsonify(
        etapa="preparacao_com_defaults",
        estrutura_validada=True,
        usa_defaults=True,
        # Ainda não representa as condições operativas reais do deck.
        pronto_para_modelo=False,
        sistema=sistema,
        metadados={
            "deck": deck["nome"],
            "data_referencia": deck["data_referencia"],
            "submercado": submercado,
        },

        selecao={
            "codigos_uhe": [u["codigo"] for u in uhe],
            "codigos_ute": [u["codigo"] for u in ute],
            "codigo_submercado": submercado["codigo"],
            "multiplicador_carga": multiplicador_carga,
            "parametros_uhe": parametros_uhe,
        },
        uhe=[
            {
                **dados,
                "volume_inicial_hm3": modelo["VI"],
                "origem_volume_inicial": "default",
                "produtibilidade": modelo["Prod"],
                "engolimento_hm3": modelo["Engol"],
                "afluencia_hm3": modelo["Afl"][0],
                "intervalos_afluencia": len(modelo["Afl"]),
            }
            for dados, modelo in zip(uhe, sistema["UHE"])
        ],
        ute=[
            {
                **dados,
                "capacidade_mw": modelo["Cap"],
                "potencia_minima_mw": modelo["Pmin"],
                "custo": modelo["Custo"],
                "tempo_ligada_h": modelo["Ton_h"],
                "tempo_desligada_h": modelo["Toff_h"],
                "rampa_subida_mw": modelo["Rup"],
                "rampa_descida_mw": modelo["Rdown"],
                "status_inicial": modelo["Status_inicial"],
                "tempo_status_inicial_h": modelo["Tempo_status_inicial_h"],
                "geracao_inicial_mw": modelo["Geracao_inicial"],
                "trajetoria_partida": modelo["Traj_partida"],
                "trajetoria_parada": modelo["Traj_parada"],
                "potencias_partida": modelo["TrUp"],
                "potencias_parada": modelo["TrDn"],
                "trajetoria_inicial": modelo["Trajetoria_inicial"],
            }
            for dados, modelo in zip(ute, sistema["UTE"])
        ],

        periodos=[],
        carga=carga,
        afluencias=[],

    )


@app.post("/api/caso/otimizar")
def api_otimizar_caso():
    """Executa o despacho MILP do sistema preparado na sessão."""

    dados = receber_json()
    deck_id = dados.get("deck_id")
    if not isinstance(deck_id, str) or deck_id != session.get("deck_id"):
        abort(409, description="Carregue e prepare o deck novamente nesta sessão.")

    deck = decks.get(deck_id)
    sistema = deck.get("sistema") if deck is not None else None
    if sistema is None:
        abort(409, description="Prepare os dados antes de executar a otimização.")

    try:
        from otimizador import despacho_milp

        resultado = despacho_milp(
            sistema,
            [uhe["VI"] for uhe in sistema["UHE"]],
            [uhe["Afl"] for uhe in sistema["UHE"]],
            cortes=[],
            iest=0,
        )
    except ImportError as erro:
        app.logger.exception("Dependência do otimizador indisponível")
        return jsonify(
            erro="O otimizador requer a dependência PySCIPOpt instalada no ambiente Python.",
            detalhe=str(erro),
        ), 503
    except (ValueError, RuntimeError, KeyError, TypeError) as erro:
        app.logger.exception("Falha na otimização")
        return jsonify(erro=f"Não foi possível executar a otimização: {erro}"), 400

    for dados_ute, resultado_ute in zip(sistema["UTE"], resultado["UTE"]):
        resultado_ute["Nome"] = dados_ute["Nome"]
    for dados_uhe, resultado_uhe in zip(sistema["UHE"], resultado["UHE"]):
        resultado_uhe["Nome"] = dados_uhe["Nome"]

    return jsonify(etapa="otimizacao_concluida", resultado=resultado)


# 6. Devolve erros das rotas da API em JSON.
@app.errorhandler(HTTPException)
def erro_http(erro):
    if request.path.startswith("/api/"):
        resposta = erro.get_response()

        resposta.data = app.json.dumps({
            "erro": erro.description
        })

        resposta.content_type = "application/json"

        return resposta

    return erro


# 7. Inicia o servidor ao executar "python app.py".
if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False,
    )
