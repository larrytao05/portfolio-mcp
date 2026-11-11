from unittest.mock import Mock

import pytest

from portfolio_mcp.api import create_app
from portfolio_mcp.execution import FixtureExecutionProvider
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.server import create_server
from portfolio_mcp.trading_service import (
    OrderSubmissionService,
    allow_fixture_submission,
)


class CustomExecutionProvider(FixtureExecutionProvider):
    pass


@pytest.mark.parametrize("factory", [create_app, create_server])
def test_factory_keeps_fixture_default(factory, tmp_path) -> None:
    factory(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
    )


@pytest.mark.parametrize("factory", [create_app, create_server])
@pytest.mark.parametrize("injected_service", [False, True])
def test_factory_requires_explicit_validator_for_custom_execution(
    factory, tmp_path, injected_service
) -> None:
    kwargs = {}
    if injected_service:
        kwargs["submission_service"] = Mock(spec=OrderSubmissionService)

    with pytest.raises(ValueError, match="policy validator"):
        factory(
            FixturePortfolioProvider(),
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
            execution_provider=CustomExecutionProvider(),
            **kwargs,
        )


@pytest.mark.parametrize("factory", [create_app, create_server])
def test_factory_accepts_explicit_validator_for_custom_execution(
    factory, tmp_path
) -> None:
    factory(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
        execution_provider=CustomExecutionProvider(),
        submission_validator=allow_fixture_submission,
    )
