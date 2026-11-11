# 	Organizar os dados selecionados na estrutura esperada pelo modelo

"""Adapta os catálogos já extraídos ao formato de entrada do modelo.

Não relê o deck nem executa otimização. Campos não extraídos pelo leitor
atual recebem defaults explícitos. Cada UTE selecionada é representada
provisoriamente por um único gerador equivalente com os mesmos defaults.
"""

from copy import deepcopy
from math import isfinite


CARGA_HORARIA_PADRAO = [
    45., 44., 43., 42., 42., 44.,
    48., 52., 56., 58., 58., 57.,
    56., 55., 56., 58., 62., 66.,
    66., 62., 56., 52., 49., 46.,
]

DEFAULTS_GERAL = {
    "Custo_def": 500.0,
    "Carga": [potencia for potencia in CARGA_HORARIA_PADRAO for _ in range(2)],
    "Duracao_h": [0.5] * 48,
}

DEFAULTS_UHE = {
    "Fracao_volume_util_inicial": 0.5,
    "Prod": 0.26,
    "Engol": 317.9,
    "Afluencia_hm3s": 0.13,
}

# Um único padrão para todas as UTEs; não atribuímos GT_1/GT_2 pela ordem.
DEFAULTS_UTE = {
    "Cap": 15.0,
    "Pmin": 5.0,
    "Custo": 10.0,
    "Ton_h": 4.0,
    "Toff_h": 2.0,
    "Rup": 4.0,
    "Rdown": 6.0,
    "Status_inicial": 1,
    "Tempo_status_inicial_h": 2.0,
    "Geracao_inicial": 8.0,
    "Traj_partida": [(0.5, 2.5), (1.0, 5.0)],
    "Traj_parada": [(0.5, 2.5), (1.0, 0.0)],
    "Trajetoria_inicial": None,
}


def _numero_deck(valor, campo):
    """Converte um valor textual do deck e rejeita campos vazios."""

    texto = str(valor).strip()
    if not texto:
        raise ValueError(f"{campo} vazio no deck.")

    return _numero(texto.replace("D", "E").replace("d", "e"), campo)


def _linhas_por_codigo(tabela, coluna, codigo):
    """Seleciona linhas de uma tabela PySDDP pelo código da usina."""

    if tabela is None or coluna not in tabela.columns:
        return []

    return [
        registro
        for registro in tabela.to_dict(orient="records")
        if str(registro[coluna]).strip() == str(codigo).strip()
    ]


def _parametros_ute_do_deck(caso, codigo):
    """Lê e agrega os parâmetros da UTE selecionada nos arquivos do deck."""

    if caso is None:
        return {}

    cadastro = getattr(caso, "cadterm", None)
    operacao = getattr(caso, "operut", None)
    unidades = _linhas_por_codigo(
        getattr(cadastro, "cadunidt_df", None),
        "num_usi",
        codigo,
    )
    iniciais = _linhas_por_codigo(
        getattr(operacao, "condicoes_iniciais_df", None),
        "us",
        codigo,
    )
    operacionais = _linhas_por_codigo(
        getattr(operacao, "limites_condicoes_df", None),
        "us",
        codigo,
    )

    parametros = {}

    def soma(linhas, coluna, campo):
        valores = [
            _numero_deck(linha[coluna], campo)
            for linha in linhas
            if coluna in linha and str(linha[coluna]).strip()
        ]
        if valores:
            parametros[campo] = sum(valores)

    def maximo(linhas, coluna, campo):
        valores = [
            _numero_deck(linha[coluna], campo)
            for linha in linhas
            if coluna in linha and str(linha[coluna]).strip()
        ]
        if valores:
            parametros[campo] = max(valores)

    # CADUNIDT: uma usina pode possuir mais de uma unidade geradora.
    soma(unidades, "pot", "Cap")
    soma(unidades, "ger_min", "Pmin")
    maximo(unidades, "temp_on", "Ton_h")
    maximo(unidades, "temp_off", "Toff_h")
    soma(unidades, "ramp_tom", "Rup")
    soma(unidades, "ramp_alivio", "Rdown")

    # INIT: geração inicial é agregada; status e tempo representam a usina.
    if iniciais:
        parametros["Status_inicial"] = int(
            _numero_deck(iniciais[0]["st"], "Status_inicial")
        )
        parametros["Geracao_inicial"] = sum(
            _numero_deck(linha["GerInic"], "Geracao_inicial")
            for linha in iniciais
        )
        parametros["Tempo_status_inicial_h"] = _numero_deck(
            iniciais[0]["tempo"],
            "Tempo_status_inicial_h",
        )

    # OPER: usa o primeiro custo informado para a usina selecionada.
    for linha in operacionais:
        if str(linha.get("Custo", "")).strip():
            parametros["Custo"] = _numero_deck(
                linha["Custo"],
                "Custo",
            )
            break

    return parametros


def montar_sistema(hidreletricas, termeletricas, caso=None, carga=None):
    """Recebe as usinas SELECIONADAS e retorna apenas Geral, UHE e UTE."""
    geral = deepcopy(DEFAULTS_GERAL)
    if carga is not None:
        geral["Carga"] = [float(valor) for valor in carga]
        geral["Duracao_h"] = [0.5] * len(geral["Carga"])
    geral["Nr_Est"] = len(geral["Duracao_h"])
    n_periodos = geral["Nr_Est"]

    fracao = _numero(DEFAULTS_UHE["Fracao_volume_util_inicial"], "Fração do volume útil")
    if not 0 <= fracao <= 1:
        raise ValueError("A fração inicial do volume útil deve estar entre 0 e 1.")

    lista_uhe = []
    for dados in hidreletricas:
        vmin = _numero(dados["volume_min_hm3"], f"Vmin de {dados['nome']}")
        vmax = _numero(dados["volume_max_hm3"], f"Vmax de {dados['nome']}")
        lista_uhe.append({
            "Nome": dados["nome"],
            "VI": vmin + fracao * (vmax - vmin),
            "Vmax": vmax,
            "Vmin": vmin,
            "Prod": DEFAULTS_UHE["Prod"],
            "Engol": DEFAULTS_UHE["Engol"],
            "Afl": [DEFAULTS_UHE["Afluencia_hm3s"]] * n_periodos,
        })

    lista_ute = []
    for dados in termeletricas:
        parametros = deepcopy(DEFAULTS_UTE)
        parametros.update(
            _parametros_ute_do_deck(
                caso,
                dados["codigo"],
            )
        )

        # Mantém as trajetórias default compatíveis com os limites reais.
        parametros["Traj_partida"] = [
            (0.5, parametros["Pmin"]),
            (1.0, parametros["Pmin"]),
        ]
        parametros["Traj_parada"] = [
            (0.5, parametros["Pmin"]),
            (1.0, 0.0),
        ]
        parametros["TrUp"] = [potencia for _, potencia in parametros["Traj_partida"]]
        parametros["TrDn"] = [potencia for _, potencia in parametros["Traj_parada"]]

        lista_ute.append({"Nome": dados["nome"], **parametros})

    sistema = {"Geral": geral, "UHE": lista_uhe, "UTE": lista_ute}
    validar_sistema(sistema)
    return sistema


def origens_dados():
    """Mantém a rastreabilidade fora do dicionário exigido pelo modelo."""
    return {
        "Geral": {
            "Custo_def": "Default, em R$/MWh.",
            "Carga": "Default: perfil fictício de 24 horas repetido em meias horas; não é a carga do submercado.",
            "Duracao_h": "Default: 48 intervalos de 0,5 h; ainda não extraído de TM.",
            "Nr_Est": "Calculado como len(Duracao_h).",
        },
        "UHE": {
            "Nome": "HIDR: nome cadastral.",
            "Vmin": "HIDR: vol_min, em hm³; sem aplicar alterações de AC/restrições operativas.",
            "Vmax": "HIDR: vol_max, em hm³; sem aplicar alterações de AC/restrições operativas.",
            "VI": f"Default calculado: Vmin + {DEFAULTS_UHE['Fracao_volume_util_inicial']} × (Vmax − Vmin); não é o volume inicial do deck.",
            "Prod": "Default constante em MW/(m³/s); não é a produtibilidade específica do HIDR.",
            "Engol": "Default em m³/s.",
            "Afl": "Default determinístico em m³/s por intervalo; não extraído do DADVAZ e sem tratamento da cascata.",
        },
        "UTE": {
            "Nome": "Nome do catálogo selecionado (TERMDAT no dashboard atual).",
            **{campo: "Default; não extraído do cadastro/OPERUT/RAMPAS."
               for campo in DEFAULTS_UTE},
            "Representacao": "Um gerador equivalente por usina selecionada; unidades reais ainda não detalhadas.",
        },
    }


def _numero(valor, campo):
    if isinstance(valor, bool):
        raise ValueError(f"{campo} deve ser um número finito.")
    try:
        numero = float(valor)
    except (TypeError, ValueError) as erro:
        raise ValueError(f"{campo} deve ser um número finito.") from erro
    if not isfinite(numero):
        raise ValueError(f"{campo} deve ser um número finito.")
    return numero


def _nao_negativo(valor, campo):
    numero = _numero(valor, campo)
    if numero < 0:
        raise ValueError(f"{campo} não pode ser negativo.")
    return numero


def validar_sistema(sistema):
    """Confere estrutura temporal e limites básicos; não garante viabilidade."""
    geral = sistema["Geral"]
    n = geral["Nr_Est"]
    if type(n) is not int or n <= 0:
        raise ValueError("Nr_Est deve ser um inteiro positivo.")
    if len(geral["Carga"]) != n or len(geral["Duracao_h"]) != n:
        raise ValueError("Carga e Duracao_h devem ter Nr_Est valores.")
    _nao_negativo(geral["Custo_def"], "Custo_def")
    for duracao in geral["Duracao_h"]:
        if _numero(duracao, "Duracao_h") <= 0:
            raise ValueError("As durações devem ser positivas.")
    for carga in geral["Carga"]:
        _nao_negativo(carga, "Carga")

    for uhe in sistema["UHE"]:
        vmin = _nao_negativo(uhe["Vmin"], "Vmin")
        vmax = _nao_negativo(uhe["Vmax"], "Vmax")
        if not vmin <= _numero(uhe["VI"], "VI") <= vmax:
            raise ValueError(f"Volumes inconsistentes em {uhe['Nome']}: esperado Vmin ≤ VI ≤ Vmax.")
        _nao_negativo(uhe["Prod"], "Prod")
        _nao_negativo(uhe["Engol"], "Engol")
        if len(uhe["Afl"]) != n:
            raise ValueError(f"Afl de {uhe['Nome']} deve ter {n} valores.")
        for vazao in uhe["Afl"]:
            _nao_negativo(vazao, "Afl")

    for ute in sistema["UTE"]:
        cap = _nao_negativo(ute["Cap"], "Cap")
        pmin = _nao_negativo(ute["Pmin"], "Pmin")
        if pmin > cap:
            raise ValueError(f"Pmin maior que Cap em {ute['Nome']}.")
        _numero(ute["Custo"], "Custo")
        for campo in ("Ton_h", "Toff_h", "Rup", "Rdown", "Tempo_status_inicial_h"):
            _nao_negativo(ute[campo], campo)
        status = ute["Status_inicial"]
        if type(status) is not int or status not in (0, 1):
            raise ValueError("Status_inicial deve ser 0 ou 1.")
        if ute["Trajetoria_inicial"] is not None:
            raise ValueError("A adaptação da trajetória inicial ainda não foi implementada.")
        geracao = _nao_negativo(ute["Geracao_inicial"], "Geracao_inicial")
        if (status == 0 and geracao != 0) or (status == 1 and not pmin <= geracao <= cap):
            raise ValueError(f"Geração inicial incompatível com o status/limites em {ute['Nome']}.")
        for campo, final in (("Traj_partida", pmin), ("Traj_parada", 0.0)):
            anterior = 0.0
            pontos = ute[campo]
            if not pontos:
                raise ValueError(f"{campo} deve conter pontos de trajetória.")
            for tempo, potencia in pontos:
                tempo = _numero(tempo, campo)
                potencia = _nao_negativo(potencia, campo)
                if tempo <= anterior or potencia > cap:
                    raise ValueError(f"Tempos/potências inválidos em {campo} de {ute['Nome']}.")
                anterior = tempo
            if abs(float(pontos[-1][1]) - final) > 1e-8:
                raise ValueError(f"{campo} de {ute['Nome']} deve terminar em {final} MW.")
