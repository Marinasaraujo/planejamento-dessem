#  Usar o PySDDP para abrir o deck e extrair seus dados

"""Primeira etapa: abrir o deck e consultar o cadastro hidráulico."""

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
        choices=["uhe", "ute"],
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