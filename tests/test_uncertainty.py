import pytest
from backtest.monte_carlo import simulate


def test_block_bootstrap_refuses_to_infer_from_tiny_sample():
    assert simulate([.01,-.01])["simulations"]==0


def test_block_bootstrap_reproducible_includes_first_loss_drawdown():
    a=simulate([-.01]*25,simulations=10,block_size=5)
    assert a==simulate([-.01]*25,simulations=10,block_size=5)
    assert a["median_max_drawdown"]==pytest.approx(1-.99**25)
