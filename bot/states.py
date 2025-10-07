from aiogram.fsm.state import StatesGroup, State

class Registration(StatesGroup):
    awaiting_contact = State()