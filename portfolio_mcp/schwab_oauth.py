import asyncio
import sys
from collections.abc import Callable, Sequence

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider


async def run_authorization(
    provider: SchwabMarketDataProvider,
    *,
    read_redirect_url: Callable[[], str],
    write: Callable[[str], None],
) -> None:
    write(f"Open this URL in a browser: {provider.authorization_url()}")
    redirect_url = read_redirect_url().strip()
    code = provider.authorization_code_from_redirect_url(redirect_url)
    refresh_token = await provider.exchange_authorization_code(code)
    write("Replace the existing value in .env with:")
    write(f"SCHWAB_REFRESH_TOKEN={refresh_token}")


def main(argv: Sequence[str] | None = None) -> None:
    if argv:
        raise SystemExit("This command does not accept arguments")

    provider = SchwabMarketDataProvider(SchwabMarketDataSettings.from_environment())
    asyncio.run(
        run_authorization(
            provider,
            read_redirect_url=lambda: input("Paste the full redirect URL: "),
            write=print,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
