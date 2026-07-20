"""FSM state groups for Xcrow."""
from aiogram.fsm.state import State, StatesGroup


class GroupDealStates(StatesGroup):
    """States used inside an escrow group during the deal workflow."""
    awaiting_seller_address   = State()  # Step 2: seller types payout address
    awaiting_deal_description = State()  # Step 4: seller/buyer types deal description
    awaiting_deal_amount      = State()  # Step 4: types amount
    awaiting_dispute_reason   = State()  # dispute flow
    awaiting_admin_note       = State()  # admin adds note to deal


class DmStates(StatesGroup):
    """States used in private DM conversations."""
    awaiting_feedback   = State()
    awaiting_wallet_add = State()
