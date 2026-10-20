"""客服分流团队：cs-triage(supervisor) + cs-faq + cs-refund（D3-5 TeamBuilder）。

在 app/main.py 装配时构建 Team，经 app/api/teams.py 自定义路由暴露触发入口。
"""

from lubanagent.multiagent import Team, TeamBuilder


def build_cs_team() -> Team:
    """构建客服分流团队。supervisor=cs-triage，成员=cs-faq/cs-refund。"""
    builder = TeamBuilder(
        "cs-team",
        description="客服分流团队：triage 调度 + faq/refund 成员",
    )
    builder.supervisor("cs-triage")
    builder.agent("cs-faq", description="退货政策咨询")
    builder.agent("cs-refund", description="退款执行")
    builder.route(max_delegations=4)
    return builder.build()


__all__ = ["build_cs_team"]
