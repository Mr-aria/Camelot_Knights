from aiogram.fsm.state import State, StatesGroup

class AdminReplyState(StatesGroup):
    waiting_for_reply = State()

class RestoreState(StatesGroup):
    waiting_for_backup_file = State()