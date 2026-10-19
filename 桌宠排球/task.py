"""Independent volleyball skill launched by the normal task process manager."""
from huanzhizhi.task_sdk import TaskSession, describe


def main():
    if describe(__file__):
        return 0
    session = TaskSession(__file__)
    try:
        from game import run
        detail = run(session)
        session.emit_finished('已停止' if session.stop_requested else '比赛结束',
                              session.skill['name'], detail,
                              stopped_by_request=session.stop_requested)
    except Exception as error:
        session.emit_finished('无法开始比赛', session.skill['name'], str(error),
                              error=f'{type(error).__name__}: {error}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
