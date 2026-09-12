from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio_mcp.models import (
    Account,
    HoldingsSnapshot,
    Instrument,
    Position,
    Quote,
    Transaction,
    TransactionHistory,
)
from portfolio_mcp.provider import AccountNotFoundError, InstrumentNotFoundError


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
        self._accounts_by_id = {account.id: account for account in self._accounts}

    async def list_accounts(self) -> list[Account]:
        return self._accounts.copy()

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        account = self._get_account(account_id)
        return HoldingsSnapshot(
            account=account,
            as_of=self.as_of,
            positions=tuple(self._positions[account_id]),
        )

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory:
        account = self._get_account(account_id)
        transactions = tuple(
            transaction
            for transaction in self._transactions
            if transaction.account_id == account_id
            and start_date <= transaction.occurred_on <= end_date
        )
        return TransactionHistory(
            account=account,
            start_date=start_date,
            end_date=end_date,
            transactions=transactions,
        )

    def _get_account(self, account_id: str) -> Account:
        account = self._accounts_by_id.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Account not found: {account_id}")
        return account


class FixtureMarketDataProvider:
    source = "fixture_market_data"
    observed_at = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)

    def __init__(self) -> None:
        instruments = (
            Instrument(
                id="us-etf:VTI",
                symbol="VTI",
                name="Vanguard Total Stock Market ETF",
                asset_class="equity_etf",
                exchange="NYSE Arca",
                currency="USD",
            ),
            Instrument(
                id="us-equity:NVDA",
                symbol="NVDA",
                name="NVIDIA Corporation",
                asset_class="equity",
                exchange="NASDAQ",
                currency="USD",
            ),
            Instrument(
                id="us-etf:GLD",
                symbol="GLD",
                name="SPDR Gold Shares",
                asset_class="commodity_etf",
                exchange="NYSE Arca",
                currency="USD",
            ),
            Instrument(
                id="us-fund:FIXTURE_UNAVAILABLE",
                symbol="FIXTURE_UNAVAILABLE",
                name="Fixture Unavailable Price Fund",
                asset_class="mutual_fund",
                exchange=None,
                currency="USD",
            ),
        )
        self._instruments = {instrument.id: instrument for instrument in instruments}
        self._prices = {
            "us-etf:VTI": (Decimal("333.33"), Decimal("333.30"), Decimal("333.36")),
            "us-equity:NVDA": (
                Decimal("200.00"),
                Decimal("199.95"),
                Decimal("200.05"),
            ),
            "us-etf:GLD": (Decimal("250.00"), Decimal("249.90"), Decimal("250.10")),
            "us-fund:FIXTURE_UNAVAILABLE": (None, None, None),
        }

    async def search_instruments(self, query: str) -> list[Instrument]:
        normalized = query.casefold().strip()
        if not normalized:
            return []

        return [
            instrument
            for instrument in self._instruments.values()
            if normalized in instrument.symbol.casefold()
            or normalized in instrument.name.casefold()
        ]

    async def get_quote(self, instrument_id: str) -> Quote:
        instrument = self._instruments.get(instrument_id)
        prices = self._prices.get(instrument_id)
        if instrument is None or prices is None:
            raise InstrumentNotFoundError("Instrument not found")

        last_price, bid_price, ask_price = prices
        return Quote(
            instrument=instrument,
            source=self.source,
            observed_at=self.observed_at,
            last_price=last_price,
            bid_price=bid_price,
            ask_price=ask_price,
            currency=instrument.currency,
        )
