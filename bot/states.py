from aiogram.fsm.state import StatesGroup, State

class Registration(StatesGroup):
    awaiting_contact = State()

class InvitationCreation(StatesGroup):
    awaiting_account_name = State()
    awaiting_mobile_number = State()
    awaiting_role = State() 