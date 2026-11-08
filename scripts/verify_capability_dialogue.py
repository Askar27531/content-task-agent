"""Real model check: unsupported explicit brief vs capability-only question."""
from test_backend_boundaries import Audit, SCENARIOS


def main():
    audit=Audit('delivery-fix/dialogue-recheck')
    case=next(case for case in SCENARIOS if case[0]=='13-unsupported')
    audit.scenario(*case)
    audit.scenario('25-capability-question',
        ['你有付款或登录抖音的工具吗？只询问能力，不改活动需求，不要创建计划或启动任务。'],
        {'no_run':True,'no_plan':True,'topic_unset':True,'budget_unset':True,'deadline_unset':True})
    if any(not result['passed'] for result in audit.results):raise SystemExit(1)


if __name__=='__main__':main()
