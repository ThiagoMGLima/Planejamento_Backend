"""Quem é o dono: resolução do perfil e provisionamento de conta (0B.3/0B.6).

Este módulo é o **único** lugar que responde "de quem são estes dados". Hoje a
resposta é sempre o perfil local, porque não há login; no PR2 muda só o corpo de
`perfil_do_request` — o resto do backend já pergunta pelo `dono` em vez de
assumir que existe um só. Esse é o critério de pronto do PR1.

O seed das 5 classes padrão desceu da migration 0002 para cá (0B.6): no migrate
ele era global e não sabia para quem semear; aqui roda por perfil, na criação.
"""

from planner.models import Classe, Perfil

# PK sentinel do perfil local, também gravada pela migration 0008. Fixa de
# propósito: código e histórico precisam apontar para a mesma linha, e um UUID
# constante sobrevive a mudanças de e-mail/nome.
UUID_PERFIL_LOCAL = "00000000-0000-0000-0000-000000000001"
EMAIL_PERFIL_LOCAL = "local@planejador.local"

CLASSES_PADRAO = [
    # (nome, cor, rastreia_conclusao) — Handoff §4.1
    ("Aula", "#e6f1fb", False),
    ("Tarefas básicas", "#f0efe9", False),
    ("Estudar", "#ecf4df", True),
    ("Prova", "#fbeaea", False),
    ("Trabalho", "#e1f5ee", True),
]


def seed_classes_padrao(perfil):
    """Dá a um perfil novo as 5 classes padrão. Idempotente.

    Sem isto, uma conta recém-criada abriria o app sem nenhuma classe — e
    `Evento.classe` é obrigatório, então ela não conseguiria criar nada.
    """
    criadas = []
    for nome, cor, rastreia in CLASSES_PADRAO:
        classe, _ = Classe.objects.do_dono(perfil).get_or_create(
            dono=perfil,
            nome=nome,
            defaults={"cor": cor, "rastreia_conclusao": rastreia},
        )
        criadas.append(classe)
    return criadas


def perfil_local():
    """O perfil dono de tudo enquanto não há autenticação.

    Criado pela migration 0008; recriado aqui se sumir (banco zerado à mão num
    ambiente de dev, por exemplo) para o app nunca ficar sem dono.
    """
    perfil, criado = Perfil.objects.get_or_create(
        id=UUID_PERFIL_LOCAL,
        defaults={
            "email": EMAIL_PERFIL_LOCAL,
            "nome": "Perfil local",
            "plano": Perfil.Plano.PRO,
        },
    )
    if criado:
        seed_classes_padrao(perfil)
    return perfil


def perfil_do_request(request):
    """O dono dos dados desta requisição.

    **É aqui que o PR2 entra.** Hoje devolve sempre o perfil local: a API roda
    aberta em localhost, sem login. Quando o `SupabaseJWTAuthentication` existir,
    esta função passa a devolver `request.user.perfil` (provisionado JIT no 1º
    acesso) e nada mais no backend precisa mudar.
    """
    return perfil_local()
