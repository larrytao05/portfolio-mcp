from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from portfolio_mcp.database import PortfolioRepository, StoredTradingSettings
from portfolio_mcp.models import AccountCapabilities


class TradingSettingsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TradingSettingsService:
    def __init__(
        self,
        repository: PortfolioRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def get(self) -> StoredTradingSettings:
        return self._repository.trading_settings()

    def replace(
        self,
        *,
        live_trading_enabled: bool,
        kill_switch_active: bool,
        max_order_shares: str | None,
        max_order_notional_usd: str | None,
        version: int,
    ) -> StoredTradingSettings:
        shares = _limit(max_order_shares, "max_order_shares")
        notional = _limit(max_order_notional_usd, "max_order_notional_usd")
        if live_trading_enabled and (shares is None or notional is None):
            raise TradingSettingsError(
                "validation_error",
                "Positive share and USD-notional limits are required to enable trading",
            )
        saved = self._repository.replace_trading_settings(
            live_trading_enabled=live_trading_enabled,
            kill_switch_active=kill_switch_active,
            max_order_shares=shares,
            max_order_notional_usd=notional,
            expected_version=version,
            updated_at=_utc_now(self._clock),
        )
        if saved is None:
            raise TradingSettingsError(
                "settings_conflict", "Trading settings changed; reload and try again"
            )
        return saved


def settings_dict(settings: StoredTradingSettings) -> dict[str, object]:
    if not settings.live_trading_enabled:
        state = "Trading blocked"
    elif settings.kill_switch_active:
        state = "Enabled but kill switch active"
    else:
        state = "Trading enabled"
    return {
        "live_trading_enabled": settings.live_trading_enabled,
        "kill_switch_active": settings.kill_switch_active,
        "max_order_shares": (
            str(settings.max_order_shares)
            if settings.max_order_shares is not None
            else None
        ),
        "max_order_notional_usd": (
            str(settings.max_order_notional_usd)
            if settings.max_order_notional_usd is not None
            else None
        ),
        "updated_at": settings.updated_at.isoformat() if settings.updated_at else None,
        "version": settings.version,
        "effective_state": state,
    }


def _limit(value: str | None, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise TradingSettingsError(
            "validation_error", f"{field} must be numeric"
        ) from error
    if not parsed.is_finite() or parsed <= 0 or parsed.adjusted() > 12:
        raise TradingSettingsError(
            "validation_error", f"{field} must be a positive limit"
        )
    exponent = parsed.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > 8:
        raise TradingSettingsError(
            "validation_error", f"{field} has too much precision"
        )
    return parsed


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class TradeIntent:
    account_id: str
    symbol: str
    asset_class: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    bid_price: Decimal | None
    ask_price: Decimal | None
    last_price: Decimal | None


@dataclass(frozen=True)
class GuardViolation:
    code: str
    message: str


@dataclass(frozen=True)
class GuardDecision:
    violations: tuple[GuardViolation, ...]
    estimated_notional: Decimal | None

    @property
    def allowed(self) -> bool:
        return not self.violations


class TradingGuard:
    def __init__(
        self,
        repository: PortfolioRepository,
        settings_service: TradingSettingsService,
    ) -> None:
        self._repository = repository
        self._settings_service = settings_service

    def evaluate(self, intent: TradeIntent) -> GuardDecision:
        settings = self._settings_service.get()
        violations = [
            *self._settings_violations(settings),
            *self._capability_violations(intent),
            *self._quantity_violations(intent),
        ]
        estimated_notional = self._notional(intent)
        if estimated_notional is None:
            violations.append(
                GuardViolation(
                    "quote_unavailable", "A price is required to evaluate this order."
                )
            )
        violations.extend(self._limit_violations(settings, intent, estimated_notional))
        violations.extend(self._holdings_violations(intent))
        return GuardDecision(tuple(violations), estimated_notional)

    def _settings_violations(
        self, settings: StoredTradingSettings
    ) -> list[GuardViolation]:
        violations = []
        if not settings.live_trading_enabled:
            violations.append(
                GuardViolation("trading_disabled", "Live trading is disabled.")
            )
        if settings.kill_switch_active:
            violations.append(
                GuardViolation("kill_switch_active", "The kill switch is active.")
            )
        return violations

    def _capability_violations(self, intent: TradeIntent) -> list[GuardViolation]:
        capability = self._repository.account_capability(intent.account_id)
        if capability is None or not capability.is_trade_capable:
            return [
                GuardViolation(
                    "capability_unavailable",
                    "Current account capability is unavailable for trading.",
                )
            ]
        if self._capability_supports(capability, intent):
            return []
        return [
            GuardViolation(
                "capability_unsupported",
                "This account does not support the requested order.",
            )
        ]

    def _quantity_violations(self, intent: TradeIntent) -> list[GuardViolation]:
        if (
            intent.quantity > 0
            and intent.quantity == intent.quantity.to_integral_value()
        ):
            return []
        return [
            GuardViolation(
                "invalid_quantity", "A positive whole-share quantity is required."
            )
        ]

    def _limit_violations(
        self,
        settings: StoredTradingSettings,
        intent: TradeIntent,
        estimated_notional: Decimal | None,
    ) -> list[GuardViolation]:
        violations = []
        if (
            settings.max_order_shares is not None
            and intent.quantity > settings.max_order_shares
        ):
            violations.append(
                GuardViolation("share_limit_exceeded", "Order exceeds the share limit.")
            )
        if (
            estimated_notional is not None
            and settings.max_order_notional_usd is not None
            and estimated_notional > settings.max_order_notional_usd
        ):
            violations.append(
                GuardViolation(
                    "notional_limit_exceeded", "Order exceeds the USD-notional limit."
                )
            )
        return violations

    def _holdings_violations(self, intent: TradeIntent) -> list[GuardViolation]:
        if intent.side != "sell":
            return []
        positions = self._repository.list_positions(intent.account_id)
        held = sum(
            (
                position.position.quantity
                for position in positions or []
                if position.position.symbol == intent.symbol
            ),
            start=Decimal("0"),
        )
        if (
            positions
            and not any(position.is_stale for position in positions)
            and intent.quantity <= held
        ):
            return []
        return [
            GuardViolation(
                "insufficient_holdings", "Sell quantity exceeds current holdings."
            )
        ]

    def _capability_supports(
        self, capability: AccountCapabilities, intent: TradeIntent
    ) -> bool:
        asset_class_supported = intent.asset_class in capability.asset_classes
        if intent.asset_class == "equity_etf":
            asset_class_supported = bool(
                {"equity", "etf"}.intersection(capability.asset_classes)
            )
        return bool(
            asset_class_supported
            and intent.side in capability.supported_sides
            and intent.order_type in capability.order_types
            and "day" in capability.time_in_force
            and "whole_shares" in capability.sizing_modes
        )

    def _notional(self, intent: TradeIntent) -> Decimal | None:
        if intent.order_type == "limit":
            return intent.quantity * intent.limit_price if intent.limit_price else None
        price = (
            intent.ask_price if intent.side == "buy" else intent.bid_price
        ) or intent.last_price
        return intent.quantity * price if price else None
