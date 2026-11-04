#  Usar o PySDDP para abrir o deck e extrair seus dados

"""Primeira etapa: abrir o deck e consultar o cadastro hidráulico."""

import math
import math
from pathlib import Path
import unicodedata
from PySDDP.Pde import Dessem
import argparse
import json


def carregar_deck(diretorio, arquivo="dessem.arq"):
    """Retorna o objeto Dessem do PySDDP para uma pasta informada."""

    pasta = Path(diretorio).expanduser().resolve()

    if not pasta.is_dir():
        raise FileNotFoundError(
            f"Diretório não encontrado: {pasta}"
        )

    if not (pasta / arquivo).is_file():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {pasta / arquivo}"
        )

    # A importação e a leitura só acontecem ao chamar a função.
    caso = Dessem(str(pasta), arquivo)

    # O PySDDP pode continuar após falhas em alguns arquivos.
    # Nesta etapa, verificamos somente o cadastro hidráulico.
    listar_hidreletricas(caso)

    return caso


def listar_hidreletricas(caso):
    """
    Retorna registros nomeados do HIDR.

    Ainda não filtra quais UHEs estão ativas no estudo.
    """

    hidr = getattr(caso, "hidr", None)

    if hidr is None:
        raise ValueError(
            "O cadastro hidráulico não está disponível no caso."
        )

    campos = [
        "codigo",
        "nome",
        "posto",
        "jusante",
        "vol_min",
        "vol_max",
    ]

    valores = {}

    # Cada campo do Hidr possui uma lista em ["valor"].
    for campo in campos:
        try:
            valores[campo] = getattr(hidr, campo)["valor"]

        except (AttributeError, KeyError, TypeError) as erro:
            raise ValueError(
                f"Campo HIDR ausente ou inválido: {campo}"
            ) from erro

    quantidade = len(valores["codigo"])

    if quantidade == 0:
        raise ValueError(
            "O cadastro hidráulico está vazio."
        )

    # As listas precisam ter o mesmo tamanho:
    # o mesmo índice deve representar a mesma usina.
    if any(
        len(lista) != quantidade
        for lista in valores.values()
    ):
        raise ValueError(
            "As listas do HIDR têm tamanhos diferentes. "
            "Confira a leitura."
        )

    # Na versão de hidr.py enviada, essa lista recebe
    # apenas os registros cuja leitura foi concluída.
    registros_completos = getattr(
        hidr,
        "_textos_originais_",
        None,
    )

    if (
        registros_completos is not None
        and len(registros_completos) != quantidade
    ):
        raise ValueError(
            "A leitura do HIDR deixou registros incompletos."
        )

    usinas = []

    for indice in range(quantidade):
        nome = (
            valores["nome"][indice]
            .replace("\x00", "")
            .strip()
        )

        # Descarta posições cadastrais sem nome.
        if not nome:
            continue

        usina = {
            "codigo": int(valores["codigo"][indice]),
            "nome": nome,
            "posto": int(valores["posto"][indice]),
            "codigo_jusante": int(
                valores["jusante"][indice]
            ),
            "volume_min_hm3": float(
                valores["vol_min"][indice]
            ),
            "volume_max_hm3": float(
                valores["vol_max"][indice]
            ),
        }

        usinas.append(usina)

    if not usinas:
        raise ValueError(
            "Nenhuma usina com nome foi encontrada no HIDR."
        )

    return sorted(
        usinas,
        key=lambda usina: (
            usina["nome"],
            usina["codigo"],
        ),
    )


def _normalizar_nome(nome):
    """Ignora acentos, diferenças de caixa e espaços repetidos com unicodedata."""

    texto = unicodedata.normalize("NFKD", nome)

    texto = "".join(
        letra
        for letra in texto
        if not unicodedata.combining(letra)
    )

    return " ".join(texto.upper().split())


def buscar_hidreletrica(caso, *, nome=None, codigo=None):
    """Busca por nome exato normalizado OU código."""

    # Exige exatamente uma das duas opções.
    if (nome is None) == (codigo is None):
        raise ValueError(
            "Informe somente nome ou somente codigo."
        )

    usinas = listar_hidreletricas(caso)

    if codigo is not None:
        encontradas = [
            usina
            for usina in usinas
            if str(usina["codigo"]) == str(codigo).strip()
        ]

    else:
        procurado = _normalizar_nome(nome)

        encontradas = [
            usina
            for usina in usinas
            if _normalizar_nome(usina["nome"]) == procurado
        ]

    if not encontradas:
        identificacao = nome if nome is not None else codigo

        raise ValueError(
            f"Usina não encontrada: {identificacao}"
        )

    if len(encontradas) > 1:
        codigos = [
            usina["codigo"]
            for usina in encontradas
        ]

        raise ValueError(
            f"Busca ambígua. Use um dos códigos: {codigos}"
        )

    return encontradas[0]

def listar_termeletricas(caso, fonte="termdat"):
    """Lista código e nome das térmicas pela fonte escolhida."""

    # 1. Identifica quais tabelas consultar.
    if fonte == "termdat":
        cadastro = getattr(caso, "cadterm", None)

        tabelas = [
            getattr(cadastro, "cadusit_df", None)
        ]

        coluna_codigo = "num_usi"

    elif fonte == "operut":
        operacao = getattr(caso, "operut", None)

        # Primeiro consulta OPER; INIT complementa a lista.
        tabelas = [
            getattr(operacao, "limites_condicoes_df", None),
            getattr(operacao, "condicoes_iniciais_df", None),
        ]

        coluna_codigo = "us"

    else:
        raise ValueError(
            "fonte deve ser 'termdat' ou 'operut'."
        )

    # 2. Remove tabelas que não estão disponíveis.
    tabelas = [
        tabela
        for tabela in tabelas
        if tabela is not None
    ]

    if not tabelas:
        raise ValueError(
            f"As tabelas de {fonte} não estão disponíveis. "
            "Confira a leitura."
        )

    # 3. Guarda uma única entrada por código de usina.
    por_codigo = {}

    for tabela in tabelas:
        obrigatorias = {coluna_codigo, "nome"}

        if not obrigatorias.issubset(tabela.columns):
            raise ValueError(
                f"Colunas esperadas em {fonte}: "
                f"{sorted(obrigatorias)}. "
                f"Encontradas: {list(tabela.columns)}"
            )

        registros = tabela[
            [coluna_codigo, "nome"]
        ].itertuples(index=False, name=None)

        for codigo_bruto, nome_bruto in registros:
            try:
                codigo = int(str(codigo_bruto).strip())

            except (ValueError, TypeError) as erro:
                raise ValueError(
                    f"Código térmico inválido em {fonte}: "
                    f"{codigo_bruto!r}"
                ) from erro

            if (
                not isinstance(nome_bruto, str)
                or not nome_bruto.strip()
            ):
                raise ValueError(
                    f"Nome térmico ausente em {fonte}, "
                    f"código {codigo}."
                )

            nome = nome_bruto.strip()

            if codigo not in por_codigo:
                por_codigo[codigo] = {
                    "codigo": codigo,
                    "nome": nome,
                }

    # 4. Retorna a lista ordenada pelo nome.
    return sorted(
        por_codigo.values(),
        key=lambda usina: (
            usina["nome"],
            usina["codigo"],
        ),
    )

def listar_submercados(caso, incluir_ficticios=False):
    """Lista os subsistemas do registro SIST do ENTDADOS."""

    dados = getattr(caso, "dadger", None)
    bloco = getattr(dados, "bloco_sist", None)

    tabela = (
        bloco.get("df")
        if isinstance(bloco, dict)
        else None
    )

    if tabela is None:
        raise ValueError(
            "O bloco SIST do ENTDADOS não está disponível. "
            "Confira a leitura."
        )

    colunas = ["num", "mne_iden", "flag", "nome"]

    if not set(colunas).issubset(tabela.columns):
        raise ValueError(
            f"Colunas esperadas em SIST: {colunas}. "
            f"Encontradas: {list(tabela.columns)}"
        )

    submercados = []

    registros = tabela[colunas].itertuples(
        index=False,
        name=None,
    )

    for numero, sigla, flag, nome in registros:
        codigo = int(str(numero).strip())
        flag = int(str(flag).strip())

        if flag not in (0, 1):
            raise ValueError(
                f"Flag SIST inválida para o subsistema "
                f"{codigo}: {flag}"
            )

        if not isinstance(nome, str) or not nome.strip():
            raise ValueError(
                f"Nome ausente em SIST, código {codigo}."
            )

        if not isinstance(sigla, str) or not sigla.strip():
            raise ValueError(
                f"Sigla ausente em SIST, código {codigo}."
            )

        ficticio = flag == 1

        if ficticio and not incluir_ficticios:
            continue

        submercados.append({
            "codigo": codigo,
            "sigla": sigla.strip(),
            "nome": nome.strip(),
            "ficticio": ficticio,
        })

    return sorted(
        submercados,
        key=lambda submercado: submercado["codigo"],
    )


def listar_demandas(caso, codigo_submercado):
    """Retorna todos os registros DP do submercado informado em uma lista."""

    dados = getattr(caso, "dadger", None)
    bloco = getattr(dados, "bloco_dp", None)
    tabela = bloco.get("df") if isinstance(bloco, dict) else None

    if tabela is None or tabela.empty:
        raise ValueError(
            "A tabela DP do ENTDADOS não está disponível ou está vazia. "
            "Confira a leitura."
        )

    colunas = ["ss", "demanda"]
    if not set(colunas).issubset(tabela.columns):
        raise ValueError(
            f"Colunas esperadas em DP: {colunas}. "
            f"Encontradas: {list(tabela.columns)}"
        )

    try:
        codigo = int(str(codigo_submercado).strip())
    except (TypeError, ValueError) as erro:
        raise ValueError(
            f"Código de submercado inválido: {codigo_submercado!r}"
        ) from erro

    demandas = []

    for posicao, registro in enumerate(
        tabela[colunas].itertuples(index=False, name=None),
        1,
    ):
        ss, demanda = registro

        try:
            if int(str(ss).strip()) != codigo:
                continue

            valor_mw = float(
                str(demanda).strip().replace("D", "E").replace("d", "e")
            )
        except (TypeError, ValueError, OverflowError) as erro:
            raise ValueError(
                f"Registro DP {posicao} inválido: {registro!r}"
            ) from erro

        if not math.isfinite(valor_mw) or valor_mw < 0:
            raise ValueError(
                f"Registro DP {posicao}: demanda inválida: {demanda!r}"
            )

        demandas.append(valor_mw)

    if not demandas:
        raise ValueError(
            f"Nenhuma demanda encontrada para o submercado {codigo}."
        )

    return demandas

def listar_catalogos(caso, fonte_termicas="termdat"):
    """Consulta as três listas usando o mesmo deck carregado."""

    return {
        "uhe": listar_hidreletricas(caso),

        "ute": listar_termeletricas(
            caso,
            fonte=fonte_termicas,
        ),

        "submercados": listar_submercados(caso),
    }

def ler_discretizacao_temporal(caso):
    """Organiza os TM já lidos pelo PySDDP, sem consultar a demanda DP.

    Os dias mantêm a ordem cronológica do arquivo, inclusive na virada de
    mês (por exemplo, 30, 31, 1). Os índices dos períodos começam em zero.
    Um período TM não pode atravessar a meia-noite nesta implementação.
    """
    dados = getattr(caso, "dadger", None)
    bloco = getattr(dados, "bloco_tm", None)
    tabela = bloco.get("df") if isinstance(bloco, dict) else None

    if tabela is None or tabela.empty:
        raise ValueError(
            "O bloco TM do ENTDADOS está ausente ou vazio. Confira a leitura."
        )

    colunas = ["dd", "hr", "mh", "durac"]
    if not set(colunas).issubset(tabela.columns):
        raise ValueError(
            f"Colunas esperadas em TM: {colunas}. "
            f"Encontradas: {list(tabela.columns)}"
        )

    def numero(valor, campo, padrao=None):
        texto = str(valor).strip()
        if not texto and padrao is not None:
            return padrao
        try:
            n = float(texto.replace("D", "E").replace("d", "e"))
        except (ValueError, TypeError) as erro:
            raise ValueError(f"Campo TM {campo} inválido: {valor!r}") from erro
        if not math.isfinite(n):
            raise ValueError(f"Campo TM {campo} não finito: {valor!r}")
        return n

    dias = {}
    dia_anterior = None
    fim_anterior = None
    numero_periodos = 0
    duracao_total_h = 0.0

    for posicao, registro in enumerate(tabela.to_dict(orient="records"), 1):
        try:
            dia_n = numero(registro["dd"], "dd")
            hora_n = numero(registro["hr"], "hr", 0)
            meia_n = numero(registro["mh"], "mh", 0)
            duracao = numero(registro["durac"], "durac", 1.0)

            if not dia_n.is_integer() or not 1 <= dia_n <= 31:
                raise ValueError("dia deve ser inteiro entre 1 e 31")
            if int(hora_n) != hora_n or not 0 <= hora_n <= 23:
                raise ValueError("hora inicial deve ser inteira entre 0 e 23")
            if meia_n not in (0, 1):
                raise ValueError("flag de meia hora deve ser 0 ou 1")
            if duracao < 0.5 or not math.isclose(
                duracao * 2, round(duracao * 2), rel_tol=0, abs_tol=1e-8
            ):
                raise ValueError("duração deve ser múltipla positiva de 0,5 h")

            dia = int(dia_n)
            inicio = hora_n + meia_n * 0.5
            fim = inicio + duracao
            if fim > 24:
                raise ValueError(
                    "período TM atravessa a meia-noite; "
                    "esse formato exige tratamento adicional"
                )

            if dia_anterior is not None:
                if dia == dia_anterior:
                    if inicio != fim_anterior:
                        raise ValueError("lacuna, sobreposição ou ordem incorreta")
                else:
                    if dia in dias:
                        raise ValueError("dia reaparece fora da ordem cronológica")
                    if dia != dia_anterior + 1 and not (
                        dia == 1 and dia_anterior in (28, 29, 30, 31)
                    ):
                        raise ValueError("sequência de dias possui lacuna")
                    if fim_anterior != 24 or inicio != 0:
                        raise ValueError("há lacuna entre os períodos de dias consecutivos")

            item_dia = dias.setdefault(dia, {
                "Dia": dia,
                "Nr_Est": 0,
                "Inicio_h": [],
                "Duracao_h": [],
                "Intervalos": [],
            })
            rede_texto = str(registro.get("rede", "")).strip()
            rede = None if not rede_texto else numero(rede_texto, "rede")
            if rede is not None and rede not in (0, 1, 2):
                raise ValueError("flag de rede deve ser 0, 1 ou 2")
            item_dia["Inicio_h"].append(float(inicio))
            item_dia["Duracao_h"].append(float(duracao))
            item_dia["Intervalos"].append({
                "Indice": numero_periodos,
                "Inicio_h": float(inicio),
                "Fim_h": float(fim),
                "Duracao_h": float(duracao),
                "Patamar": str(registro.get("patamar", "")).strip(),
                "Rede": None if rede is None else int(rede),
            })
            item_dia["Nr_Est"] += 1
            numero_periodos += 1
            duracao_total_h += duracao
            dia_anterior, fim_anterior = dia, fim
        except (ValueError, TypeError, OverflowError) as erro:
            raise ValueError(f"Registro TM {posicao}: {erro}") from erro

    return {
        "Nr_Dias": len(dias),
        "Nr_Est": numero_periodos,
        "Duracao_total_h": float(duracao_total_h),
        "Dias": dias,
    }


# Executado somente ao rodar este arquivo diretamente.
if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Consulta usinas de um deck DESSEM."
    )

    parser.add_argument(
        "diretorio",
        help="Pasta que contém o dessem.arq",
    )

    parser.add_argument(
        "--tipo",
        choices=["uhe", "ute","dias"],
        default="uhe",
        help="Tipo de usina: uhe ou ute",
    )

    parser.add_argument(
        "--fonte",
        choices=["termdat", "operut"],
        default="termdat",
        help="Fonte usada para listar as térmicas",
    )

    busca = parser.add_mutually_exclusive_group()
    busca.add_argument("--nome", help="Nome da usina")
    busca.add_argument("--codigo", help="Código da usina")

    argumentos = parser.parse_args()

    caso = carregar_deck(argumentos.diretorio)

    if argumentos.tipo == "dias":
        if argumentos.nome is not None or argumentos.codigo is not None:
            parser.error(
                "--nome e --codigo não se aplicam à consulta de dias."
            )

        temporal = ler_discretizacao_temporal(caso)

        print(
            json.dumps(
                temporal,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )

        raise SystemExit(0)
    
    # Escolhe qual catálogo consultar.
    if argumentos.tipo == "ute":
        usinas = listar_termeletricas(
            caso,
            fonte=argumentos.fonte,
        )
    else:
        usinas = listar_hidreletricas(caso)

    # Opcionalmente, filtra por nome ou código.
    if argumentos.nome is not None:
        procurado = _normalizar_nome(argumentos.nome)

        usinas = [
            usina
            for usina in usinas
            if _normalizar_nome(usina["nome"]) == procurado
        ]

    elif argumentos.codigo is not None:
        usinas = [
            usina
            for usina in usinas
            if str(usina["codigo"]) == argumentos.codigo.strip()
        ]

    consulta_individual = (
        argumentos.nome is not None
        or argumentos.codigo is not None
    )

    if consulta_individual:
        if not usinas:
            parser.error("Nenhuma usina encontrada.")

        if len(usinas) > 1:
            parser.error(
                "Mais de uma usina encontrada. Consulte pelo código."
            )

        resultado = usinas[0]

    else:
        resultado = usinas

    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
