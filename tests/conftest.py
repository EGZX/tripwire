"""Shared test fixtures."""
import pytest


@pytest.fixture
def neutral_leverage():
    """Rate-mechanics tests isolate their subject from the leverage premium: (1 - t) = 0 makes every factor 1."""
    from screener import policy
    policy.configure({"model_policy": {"leverage_tax_rate": 1.0}})
    yield
    policy.configure({})
