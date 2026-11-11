import preparacao

"""Despacho diário com UC térmico e trajetórias das equações fornecidas do CEPEL.

Entradas e unidades (nenhuma conversão é feita):
- sistema['Geral']['Duracao_h']: horas de cada intervalo.
- sistema['Geral']['Carga']: MW de cada intervalo do dia selecionado.
- VI[i]: volume inicial da UHE i.
- AFL[i][t]: volume afluente incremental da UHE i em hm³ no intervalo t.
- VI, vt, vv e UHE['Engol']: volumes em hm³; vt, vv e Engol são por intervalo.
- UHE['Prod']: MW/hm³, aplicado diretamente ao volume turbinado.
  Engol e Prod aceitam um valor constante ou uma lista por intervalo.
- UTE['Rup'] e UTE['Rdown']: taxas máximas em MW/h. O limite em MW
  de cada intervalo é taxa * Duracao_h[t], incluindo t=0.
- UTE['TrUp'] e UTE['TrDn']: potências em MW por passo, em ordem cronológica.
  Cada passo ocupa um intervalo. Esta versão exige intervalos iguais quando
  existem trajetórias. Não interpreta automaticamente tuplas (tempo, potência).
- UTE['Trajetoria_inicial']: None ou {'Tipo': 'partida'/'parada', 'Passo': k}.
  Passo começa em 1 e indica o passo cumprido no PRIMEIRO intervalo do dia.
- cortes: cortes fornecidos para o fim do dia iest, aplicados ao volume terminal.

A geração hidráulica usa diretamente Prod * vt, sem conversão de unidades.
Esta versão não adiciona cascatas hidráulicas.
Inclui as rampas 10.17 e 10.18, usando o indicador de trajetória do período
atual, como nas equações fornecidas. Não inclui custos de acionamento/desligamento.
As trajetórias, aqui, prescrevem a potência por intervalo, não pontos de uma
curva a interpolar. Elas devem ser preparadas conforme essa convenção.
"""

from math import isfinite
from pyscipopt import Model


def despacho_milp(sistema, VI, AFL, cortes, iest, imprime=False):
    num_UHE = len(sistema['UHE'])
    num_UTE = len(sistema['UTE'])

    duracao_h = [float(valor) for valor in sistema['Geral']['Duracao_h']]
    n_periodos = len(duracao_h)
    carga = [float(valor) for valor in sistema['Geral']['Carga']]

    if n_periodos == 0 or any(not isfinite(d) or d <= 0 for d in duracao_h):
        raise ValueError('Duracao_h deve conter durações positivas e finitas.')
    if len(carga) != n_periodos or any(not isfinite(c) or c < 0 for c in carga):
        raise ValueError('Carga deve conter um valor não negativo por intervalo.')
    if len(VI) != num_UHE or len(AFL) != num_UHE:
        raise ValueError('VI e AFL devem conter dados de todas as UHEs.')
    if any(len(afluencia) != n_periodos for afluencia in AFL):
        raise ValueError('AFL deve ser indexada como AFL[i][t].')

    def valor_periodo(valor, t):
        if isinstance(valor, (list, tuple)):
            if len(valor) != n_periodos:
                raise ValueError('Parâmetro temporal deve ter n_periodos valores.')
            return float(valor[t])
        return float(valor)

    # Instante inicial de cada intervalo, em horas.
    inicio_h = []
    tempo_h = 0.0
    for duracao in duracao_h:
        inicio_h.append(tempo_h)
        tempo_h += duracao

    def janela_permanencia(t, tempo_minimo_h):
        return [
            k for k in range(t, n_periodos)
            if inicio_h[k] - inicio_h[t] < tempo_minimo_h - 1e-9
        ]

    # Dados das trajetórias, sem conversão de tempos ou unidades.
    tr_up = []
    tr_dn = []
    evento_partida_inicial = []
    evento_parada_inicial = []

    for i, ute in enumerate(sistema['UTE']):
        for chave in ('TrUp', 'TrDn'):
            if chave not in ute:
                raise ValueError(f'{ute["Nome"]}: informe {chave}, mesmo que seja [].')
            if any(isinstance(p, (list, tuple)) for p in ute[chave]):
                raise ValueError(f'{chave} deve ser uma lista de potências por passo.')

        subida = [float(p) for p in ute['TrUp']]
        descida = [float(p) for p in ute['TrDn']]
        pmin = float(ute['Pmin'])
        cap = float(ute['Cap'])
        status = ute['Status_inicial']
        geracao_inicial = float(ute['Geracao_inicial'])

        if not isfinite(geracao_inicial) or not 0 <= geracao_inicial <= cap:
            raise ValueError('Geracao_inicial deve estar entre 0 e Cap.')
        if status not in (0, 1):
            raise ValueError('Status_inicial deve ser 0 ou 1.')
        if not (0 <= pmin <= cap):
            raise ValueError('É necessário 0 <= Pmin <= Cap.')
        if any(not isfinite(p) or p < 0 or p > cap for p in subida + descida):
            raise ValueError('Potências das trajetórias devem estar entre 0 e Cap.')
        if subida and abs(subida[-1] - pmin) > 1e-9:
            raise ValueError('O último passo de TrUp deve coincidir com Pmin.')
        if descida and (abs(descida[0] - pmin) > 1e-9 or abs(descida[-1]) > 1e-9):
            raise ValueError('TrDn deve começar em Pmin e terminar em zero.')
        if any(b < a - 1e-9 for a, b in zip(subida, subida[1:])):
            raise ValueError('TrUp deve ser não decrescente.')
        if any(b > a + 1e-9 for a, b in zip(descida, descida[1:])):
            raise ValueError('TrDn deve ser não crescente.')
        if (subida or descida) and any(abs(d - duracao_h[0]) > 1e-9 for d in duracao_h):
            raise ValueError('Trajetórias por passo exigem intervalos iguais nesta versão.')
        for chave in ('Ton_h', 'Toff_h', 'Tempo_status_inicial_h', 'Rup', 'Rdown'):
            if not isfinite(float(ute[chave])) or float(ute[chave]) < 0:
                raise ValueError(f'{chave} deve ser não negativo e finito.')

        partida_inicial = None
        parada_inicial = None
        trajetoria = ute.get('Trajetoria_inicial')

        if trajetoria is None:
            if status == 0 and abs(geracao_inicial) > 1e-9:
                raise ValueError('Unidade desligada deve ter Geracao_inicial nula.')
            if status == 1 and not (pmin <= geracao_inicial <= cap):
                raise ValueError('Sem trajetória inicial, geração ligada deve estar entre Pmin e Cap.')
        else:
            tipo = trajetoria['Tipo']
            passo = trajetoria['Passo']
            if not isinstance(passo, int) or isinstance(passo, bool):
                raise ValueError('Passo da trajetória inicial deve ser inteiro.')
            if status != 1:
                raise ValueError('Trajetória inicial em andamento exige Status_inicial = 1.')
            if tipo == 'partida':
                # Primeiro passo em t=0 seria uma nova partida, não uma já iniciada.
                if not 2 <= passo <= len(subida):
                    raise ValueError('Partida inicial exige 2 <= Passo <= len(TrUp).')
                partida_inicial = -(passo - 1)
                if len(subida) - passo + 1 > n_periodos:
                    raise ValueError('Amplie o horizonte para concluir a partida inicial.')
                if abs(geracao_inicial - subida[passo - 2]) > 1e-9:
                    raise ValueError('Geracao_inicial deve coincidir com o passo anterior da partida.')
                tempo_esperado = (passo - 1) * duracao_h[0]
                if abs(float(ute['Tempo_status_inicial_h']) - tempo_esperado) > 1e-9:
                    raise ValueError('Tempo_status_inicial_h incompatível com a partida inicial.')
            elif tipo == 'parada':
                if not 1 <= passo <= len(descida):
                    raise ValueError('Parada inicial exige 1 <= Passo <= len(TrDn).')
                parada_inicial = len(descida) - passo + 1
                if passo > 1 and abs(geracao_inicial - descida[passo - 2]) > 1e-9:
                    raise ValueError('Geracao_inicial deve coincidir com o passo anterior da parada.')
                if passo == 1 and not (pmin <= geracao_inicial <= cap):
                    raise ValueError('Antes do primeiro passo da parada, geração deve estar entre Pmin e Cap.')
                if parada_inicial >= n_periodos:
                    raise ValueError('Amplie o horizonte para concluir a parada inicial.')
            else:
                raise ValueError('Tipo deve ser partida ou parada.')

        tr_up.append(subida)
        tr_dn.append(descida)
        evento_partida_inicial.append(partida_inicial)
        evento_parada_inicial.append(parada_inicial)

    modelo = Model('despacho_milp_diario')
    if not imprime:
        modelo.hideOutput()

    ### Variáveis e limites ###

    vf = []
    vt = []
    vv = []

    for i, uhe in enumerate(sistema['UHE']):
        vf.append([
            modelo.addVar(name=f'vf_{i}_{t}', vtype='C',
                          lb=float(uhe['Vmin']), ub=float(uhe['Vmax']))
            for t in range(n_periodos)
        ])
        vt.append([
            modelo.addVar(name=f'vt_{i}_{t}', vtype='C', lb=0.0,
                          ub=valor_periodo(uhe['Engol'], t))
            for t in range(n_periodos)
        ])
        vv.append([
            modelo.addVar(name=f'vv_{i}_{t}', vtype='C', lb=0.0)
            for t in range(n_periodos)
        ])

    gt = []
    ut = []
    yt = []
    wt = []

    for i, ute in enumerate(sistema['UTE']):
        gt.append([
            modelo.addVar(name=f'gt_{i}_{t}', vtype='C', lb=0.0,
                          ub=float(ute['Cap']))
            for t in range(n_periodos)
        ])
        ut.append([
            modelo.addVar(name=f'ut_{i}_{t}', vtype='B')
            for t in range(n_periodos)
        ])
        yt.append([
            modelo.addVar(name=f'yt_{i}_{t}', vtype='B')
            for t in range(n_periodos)
        ])
        wt.append([
            modelo.addVar(name=f'wt_{i}_{t}', vtype='B')
            for t in range(n_periodos)
        ])

    deficit = [
        modelo.addVar(name=f'deficit_{t}', vtype='C', lb=0.0)
        for t in range(n_periodos)
    ]
    alpha = modelo.addVar(name='custo_futuro', vtype='C', lb=0.0)

    ### Função objetivo ###

    fob = 0
    for i, ute in enumerate(sistema['UTE']):
        for t in range(n_periodos):
            # MW * horas = MWh. Não é conversão de vazão ou volume.
            fob += float(ute['Custo']) * gt[i][t] * duracao_h[t]

    for t in range(n_periodos):
        fob += float(sistema['Geral']['Custo_def']) * deficit[t] * duracao_h[t]

    for i, uhe in enumerate(sistema['UHE']):
        for t in range(n_periodos):
            # Mantém a penalidade original por unidade de volume vertido.
            fob += 0.01 * vv[i][t]

    fob += alpha
    modelo.setObjective(fob, 'minimize')

    ### Balanço hídrico ###

    balanco_hidrico = []
    for i, uhe in enumerate(sistema['UHE']):
        restricoes_uhe = []
        for t in range(n_periodos):
            volume_anterior = float(VI[i]) if t == 0 else vf[i][t - 1]
            restricao = modelo.addCons(
                vf[i][t] + vt[i][t] + vv[i][t]
                == volume_anterior + float(AFL[i][t]),
                name=f'balanco_hidrico_{i}_{t}',
            )
            restricoes_uhe.append(restricao)
        balanco_hidrico.append(restricoes_uhe)

    ### Atendimento à demanda ###

    balanco_energia = []
    for t in range(n_periodos):
        AD = 0
        for i, uhe in enumerate(sistema['UHE']):
            AD += valor_periodo(uhe['Prod'], t) * vt[i][t]
        for i, ute in enumerate(sistema['UTE']):
            AD += gt[i][t]
        AD += deficit[t]
        balanco_energia.append(modelo.addCons(
            AD == carga[t], name=f'balanco_energia_{t}',
        ))

    ### Unit Commitment térmico ###

    for i, ute in enumerate(sistema['UTE']):
        ton_h = float(ute['Ton_h'])
        toff_h = float(ute['Toff_h'])
        status_inicial = int(ute['Status_inicial'])

        for t in range(n_periodos):
            u_anterior = status_inicial if t == 0 else ut[i][t - 1]

            # Equações 10.5 e 10.6.
            modelo.addCons(
                ut[i][t] - u_anterior == yt[i][t] - wt[i][t],
                name=f'transicao_{i}_{t}',
            )
            modelo.addCons(
                yt[i][t] + wt[i][t] <= 1,
                name=f'exclusividade_eventos_{i}_{t}',
            )

            # Equações 10.3 e 10.4, limitadas ao horizonte disponível.
            janela_on = janela_permanencia(t, ton_h)
            n_on = len(janela_on)
            if n_on > 0:
                modelo.addCons(
                    sum(ut[i][k] for k in janela_on)
                    >= n_on * (ut[i][t] - u_anterior),
                    name=f'ton_{i}_{t}',
                )

            janela_off = janela_permanencia(t, toff_h)
            n_off = len(janela_off)
            if n_off > 0:
                modelo.addCons(
                    sum(1 - ut[i][k] for k in janela_off)
                    >= n_off * (u_anterior - ut[i][t]),
                    name=f'toff_{i}_{t}',
                )

        # Permanência ainda obrigatória na condição inicial.
        tempo_minimo_h = ton_h if status_inicial == 1 else toff_h
        tempo_restante_h = max(
            0.0, tempo_minimo_h - float(ute['Tempo_status_inicial_h']),
        )
        for t in range(n_periodos):
            if inicio_h[t] < tempo_restante_h - 1e-9:
                modelo.addCons(
                    ut[i][t] == status_inicial,
                    name=f'permanencia_inicial_{i}_{t}',
                )

    ### Trajetórias de acionamento e desligamento ###

    for i, ute in enumerate(sistema['UTE']):
        n_up = len(tr_up[i])
        n_dn = len(tr_dn[i])

        def evento_y(indice):
            if indice >= 0:
                return yt[i][indice]
            # Evento histórico conhecido, quando já começa em partida.
            return int(indice == evento_partida_inicial[i])

        for t in range(n_periodos):
            # Política terminal: uma nova partida deve terminar dentro do dia.
            if t + n_up > n_periodos:
                modelo.addCons(yt[i][t] == 0, name=f'partida_terminal_{i}_{t}')

            # Não permite inventar uma parada que teria começado antes do dia.
            if t < n_dn and t != evento_parada_inicial[i]:
                modelo.addCons(wt[i][t] == 0, name=f'parada_sem_historico_{i}_{t}')
            if t == evento_parada_inicial[i]:
                modelo.addCons(wt[i][t] == 1, name=f'parada_inicial_{i}_{t}')

            em_partida = sum(
                evento_y(t - k + 1) for k in range(1, n_up + 1)
            )
            em_parada = sum(
                wt[i][t + k] for k in range(1, n_dn + 1)
                if t + k < n_periodos
            )
            geracao_traj = sum(
                tr_up[i][k - 1] * evento_y(t - k + 1)
                for k in range(1, n_up + 1)
            )
            geracao_traj += sum(
                tr_dn[i][n_dn - k] * wt[i][t + k]
                for k in range(1, n_dn + 1)
                if t + k < n_periodos
            )

            operacao_normal = ut[i][t] - em_partida - em_parada
            modelo.addCons(
                em_partida + em_parada <= ut[i][t],
                name=f'compatibilidade_trajetorias_{i}_{t}',
            )
            # Equações 10.8 e 10.9.
            modelo.addCons(
                gt[i][t] >= float(ute['Pmin']) * operacao_normal + geracao_traj,
                name=f'geracao_min_{i}_{t}',
            )
            modelo.addCons(
                gt[i][t] <= float(ute['Cap']) * operacao_normal + geracao_traj,
                name=f'geracao_max_{i}_{t}',
            )

            ### Rampas de tomada e alívio de carga ###

            if t == 0:
                g_anterior = float(ute['Geracao_inicial'])
            else:
                g_anterior = gt[i][t - 1]

            # Os dados Rup/Rdown estão em MW/h. A equação usa limites em MW.
            rampa_subida = float(ute['Rup']) * duracao_h[t]
            rampa_descida = float(ute['Rdown']) * duracao_h[t]
            em_trajetoria = em_partida + em_parada

            # Equações 10.17 e 10.18.
            # Sem trajetória: diferença limitada pela rampa do intervalo.
            # Em trajetória: diferença limitada por Cap, tornando-a redundante
            # porque 0 <= gt <= Cap (inclusive na condição inicial).
            modelo.addCons(
                gt[i][t] - g_anterior
                <= rampa_subida
                + (float(ute['Cap']) - rampa_subida) * em_trajetoria,
                name=f'rampa_subida_{i}_{t}',
            )
            modelo.addCons(
                g_anterior - gt[i][t]
                <= rampa_descida
                + (float(ute['Cap']) - rampa_descida) * em_trajetoria,
                name=f'rampa_descida_{i}_{t}',
            )

    ### Cortes da função de custo futuro ###

    for k, icorte in enumerate(cortes):
        if icorte['Estagio'] == iest:
            equacao = 0
            for i in range(num_UHE):
                equacao += float(icorte['Coefs'][i]) * vf[i][n_periodos - 1]
            equacao += float(icorte['Termo_Indep'])
            modelo.addCons(alpha >= equacao, name=f'corte_{k}')

    ### Resolução ###

    modelo.optimize()
    status = modelo.getStatus()
    if status != 'optimal':
        raise RuntimeError(f'Despacho do dia/estágio {iest}: {status}')

    ### Resultados ###

    Geral = {
        # Não consulta duais diretamente do MILP.
        'CMO': None,
        'CustoTotal': modelo.getObjVal(),
        'CustoFuturo': modelo.getVal(alpha),
        'Deficit': [modelo.getVal(deficit[t]) for t in range(n_periodos)],
        'Duracao_h': duracao_h,
        'Carga': carga,
    }

    lista_uhe = []
    for i, uhe in enumerate(sistema['UHE']):
        resultado_uhe = {
            'vf': [modelo.getVal(vf[i][t]) for t in range(n_periodos)],
            'vt': [modelo.getVal(vt[i][t]) for t in range(n_periodos)],
            'vv': [modelo.getVal(vv[i][t]) for t in range(n_periodos)],
            'cma': None,
        }
        lista_uhe.append(resultado_uhe)

    lista_ute = []
    for i, ute in enumerate(sistema['UTE']):
        estados = [int(round(modelo.getVal(ut[i][t]))) for t in range(n_periodos)]
        geracoes = [modelo.getVal(gt[i][t]) for t in range(n_periodos)]
        tempo_status_h = float(ute['Tempo_status_inicial_h'])
        estado_anterior = int(ute['Status_inicial'])
        for t in range(n_periodos):
            if estados[t] != estado_anterior:
                tempo_status_h = 0.0
            tempo_status_h += duracao_h[t]
            estado_anterior = estados[t]

        minimo_final_h = float(ute['Ton_h'] if estados[-1] else ute['Toff_h'])
        resultado_ute = {
            'gt': geracoes,
            'ut': estados,
            'yt': [int(round(modelo.getVal(yt[i][t]))) for t in range(n_periodos)],
            'wt': [int(round(modelo.getVal(wt[i][t]))) for t in range(n_periodos)],
            'Estado_final': {
                'Status_inicial': estados[-1],
                'Tempo_status_inicial_h': tempo_status_h,
                'Geracao_inicial': geracoes[-1],
                # Pela política terminal, não restam trajetórias em andamento.
                'Trajetoria_inicial': None,
                'Tempo_minimo_restante_h': max(0.0, minimo_final_h - tempo_status_h),
            },
        }
        lista_ute.append(resultado_ute)

    resultado = {'Geral': Geral, 'UHE': lista_uhe, 'UTE': lista_ute}
    if imprime:
        print('Custo Total:', Geral['CustoTotal'])
        for i, uhe in enumerate(lista_uhe):
            print(sistema['UHE'][i]['Nome'], uhe)
        for i, ute in enumerate(lista_ute):
            print(sistema['UTE'][i]['Nome'], ute)
        print('Déficit por intervalo:', Geral['Deficit'])
        print('Custo futuro:', Geral['CustoFuturo'])

    return resultado
