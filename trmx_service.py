from __future__ import annotations

from fifo_chain_service import (
    create_fifo_chain_next_step,
    set_fifo_chain_default_dispatch_func,
    trigger_fifo_chain_next_step_if_needed,
)


def set_trmx_default_dispatch_func(dispatch_func):
    return set_fifo_chain_default_dispatch_func(dispatch_func)


def create_trmx_next_step(db, completed_order, config, dispatch_func=None):
    return create_fifo_chain_next_step(db, completed_order, config, dispatch_func=dispatch_func)


def trigger_trmx_next_step_if_needed(db, completed_order_id, dispatch_func=None):
    return trigger_fifo_chain_next_step_if_needed(db, completed_order_id, dispatch_func=dispatch_func)
