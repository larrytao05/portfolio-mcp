from datetime import date
from decimal import Decimal

from portfolio_mcp.models import Account, Position, Transaction
from portfolio_mcp.provider import AccountNotFoundError


class FixturePortfolioProvider:
    as_of = date(2026, 8, 29)

    def __init__(self) -> None:
        self._accounts = [
            Account(
                id="schwab-taxable-demo",
                provider="Schwab",
                label="Schwab Taxable ••••4821",
                account_type="taxable_brokerage",
                currency="USD",
            ),
            Account(
                id="fidelity-roth-demo",
                provider="Fidelity",
                label="Fidelity Roth IRA ••••9046",
                account_type="roth_ira",
                currency="USD",
            ),
        ]
        self._positions = {
            "schwab-taxable-demo": [
                Position(
                    account_id="schwab-taxable-demo",
                    symbol="VTI",
                    name="Vanguard Total Stock Market ETF",
                    asset_class="equity_etf",
                    quantity=Decimal("9"),
                    current_price=Decimal("333.33"),
                    market_value=Decimal("2999.97"),
                    cost_basis=Decimal("2781.00"),
                    currency="USD",
                ),
                Position(
                    account_id="schwab-taxable-demo",
                    symbol="GLD",
                    name="SPDR Gold Shares",
                    asset_class="commodity_etf",
                    quantity=Decimal("2.4"),
                    current_price=Decimal("250.00"),
                    market_value=Decimal("600.00"),
                    cost_basis=Decimal("540.00"),
                    currency="USD",
                ),
                Position(
                    account_id="schwab-taxable-demo",
                    symbol="VXUS",
                    name="Vanguard Total International Stock ETF",
                    asset_class="equity_etf",
                    quantity=Decimal("12.5"),
                    current_price=Decimal("64.00"),
                    market_value=Decimal("800.00"),
                    cost_basis=Decimal("744.00"),
                    currency="USD",
                ),
                Position(
                    account_id="schwab-taxable-demo",
                    symbol="NVDA",
                    name="NVIDIA Corporation",
                    asset_class="equity",
                    quantity=Decimal("2"),
                    current_price=Decimal("200.00"),
                    market_value=Decimal("400.00"),
                    cost_basis=Decimal("318.00"),
                    currency="USD",
                ),
            ],
            "fidelity-roth-demo": [
                Position(
                    account_id="fidelity-roth-demo",
                    symbol="VTI",
                    name="Vanguard Total Stock Market ETF",
                    asset_class="equity_etf",
                    quantity=Decimal("6"),
                    current_price=Decimal("333.33"),
                    market_value=Decimal("1999.98"),
                    cost_basis=Decimal("1782.00"),
                    currency="USD",
                ),
                Position(
                    account_id="fidelity-roth-demo",
                    symbol="AVUV",
                    name="Avantis U.S. Small Cap Value ETF",
                    asset_class="equity_etf",
                    quantity=Decimal("5"),
                    current_price=Decimal("100.00"),
                    market_value=Decimal("500.00"),
                    cost_basis=Decimal("460.00"),
                    currency="USD",
                ),
                Position(
                    account_id="fidelity-roth-demo",
                    symbol="BND",
                    name="Vanguard Total Bond Market ETF",
                    asset_class="bond_etf",
                    quantity=Decimal("30"),
                    current_price=Decimal("70.00"),
                    market_value=Decimal("2100.00"),
                    cost_basis=Decimal("2070.00"),
                    currency="USD",
                ),
                Position(
                    account_id="fidelity-roth-demo",
                    symbol="SPGI",
                    name="S&P Global Inc.",
                    asset_class="equity",
                    quantity=Decimal("1"),
                    current_price=Decimal("450.00"),
                    market_value=Decimal("450.00"),
                    cost_basis=Decimal("410.00"),
                    currency="USD",
                ),
                Position(
                    account_id="fidelity-roth-demo",
                    symbol="MU",
                    name="Micron Technology, Inc.",
                    asset_class="equity",
                    quantity=Decimal("2"),
                    current_price=Decimal("75.00"),
                    market_value=Decimal("150.00"),
                    cost_basis=Decimal("136.00"),
                    currency="USD",
                ),
            ],
        }
        self._transactions = [
            Transaction(
                id="schwab-demo-003",
                account_id="schwab-taxable-demo",
                occurred_on=date(2026, 8, 14),
                transaction_type="dividend",
                symbol="VTI",
                description="VTI dividend",
                quantity=None,
                amount=Decimal("15.23"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
            Transaction(
                id="schwab-demo-002",
                account_id="schwab-taxable-demo",
                occurred_on=date(2026, 8, 1),
                transaction_type="buy",
                symbol="NVDA",
                description="Bought NVIDIA Corporation",
                quantity=Decimal("2"),
                amount=Decimal("-318.00"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
            Transaction(
                id="schwab-demo-001",
                account_id="schwab-taxable-demo",
                occurred_on=date(2026, 7, 15),
                transaction_type="buy",
                symbol="VTI",
                description="Bought Vanguard Total Stock Market ETF",
                quantity=Decimal("9"),
                amount=Decimal("-2781.00"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
            Transaction(
                id="fidelity-demo-003",
                account_id="fidelity-roth-demo",
                occurred_on=date(2026, 8, 20),
                transaction_type="buy",
                symbol="BND",
                description="Bought Vanguard Total Bond Market ETF",
                quantity=Decimal("30"),
                amount=Decimal("-2070.00"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
            Transaction(
                id="fidelity-demo-002",
                account_id="fidelity-roth-demo",
                occurred_on=date(2026, 8, 5),
                transaction_type="contribution",
                symbol=None,
                description="Roth IRA contribution",
                quantity=None,
                amount=Decimal("5000.00"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
            Transaction(
                id="fidelity-demo-001",
                account_id="fidelity-roth-demo",
                occurred_on=date(2026, 7, 22),
                transaction_type="buy",
                symbol="VTI",
                description="Bought Vanguard Total Stock Market ETF",
                quantity=Decimal("6"),
                amount=Decimal("-1782.00"),
                fees=Decimal("0.00"),
                currency="USD",
            ),
        ]

    async def list_accounts(self) -> list[Account]:
        return self._accounts.copy()

    async def get_holdings(self, account_id: str) -> list[Position]:
        self._ensure_account_exists(account_id)
        return self._positions[account_id].copy()

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> list[Transaction]:
        self._ensure_account_exists(account_id)
        return [
            transaction
            for transaction in self._transactions
            if transaction.account_id == account_id
            and start_date <= transaction.occurred_on <= end_date
        ]

    def _ensure_account_exists(self, account_id: str) -> None:
        if account_id not in self._positions:
            raise AccountNotFoundError(f"Account not found: {account_id}")
