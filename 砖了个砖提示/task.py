"""Read-only game hint task discovered by the desktop-pet host."""
from __future__ import annotations

from huanzhizhi.task_sdk import TaskSession, describe, load_skill

TASK_NAME = load_skill(__file__)['name']


def main() -> int:
    if describe(__file__):
        return 0
    session = TaskSession(__file__)
    from controller import HintController
    controller = None
    try:
        controller = HintController(session)
        status,detail,stopped = controller.run()
        session.emit_finished(status,TASK_NAME,detail,stopped_by_request=stopped)
    except Exception as error:
        detail = str(error)
        if controller is not None:
            try:
                detail += f'；诊断：{controller.save_failure(error)}'
            except Exception as diagnostic_error:
                detail += f'；诊断保存失败：{diagnostic_error}'
        session.emit_finished('失败',TASK_NAME,detail,error=f'{type(error).__name__}: {error}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
