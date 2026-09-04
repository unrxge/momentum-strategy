"""Trading 212 API client for account management and data retrieval."""

import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()


class T212Client:
    """Trading 212 API client supporting both demo and live environments."""

    def __init__(self):
        """Initialize client with environment-specific credentials."""
        self.environment = os.getenv("ENVIRONMENT", "demo").lower()

        if self.environment not in ["demo", "live"]:
            raise ValueError(f"Invalid ENVIRONMENT: {self.environment}. Must be 'demo' or 'live'.")

        # Load environment-specific credentials
        env_prefix = f"T212_{self.environment.upper()}"
        self.api_key = os.getenv(f"{env_prefix}_API_KEY")
        self.api_secret = os.getenv(f"{env_prefix}_API_SECRET")
        self.base_url = os.getenv(f"{env_prefix}_BASE_URL")

        if not self.api_key or not self.api_secret or not self.base_url:
            raise ValueError(
                f"Missing T212 credentials for {self.environment.upper()} environment. "
                f"Check ENVIRONMENT and {env_prefix}_* variables in .env"
            )

        # Set up Basic Auth header
        credentials = f"{self.api_key}:{self.api_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self.headers = {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}

        print(f"✓ Connected to T212 {self.environment.upper()} environment")
        print(f"  Base URL: {self.base_url}")

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """
        Make an HTTP request to the T212 API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path (e.g., "equity/account/summary")
            **kwargs: Additional arguments to pass to requests

        Returns:
            Parsed JSON response

        Raises:
            ValueError: If API returns an error status
        """
        url = f"{self.base_url}/{endpoint}"

        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)

            # Handle authentication errors
            if response.status_code in [401, 403]:
                raise ValueError(
                    f"Authentication failed ({response.status_code}). "
                    f"Check API key validity for {self.environment.upper()} environment."
                )

            # Handle other errors
            if response.status_code >= 400:
                raise ValueError(
                    f"API request failed ({response.status_code}): {response.text}"
                )

            return response.json()

        except requests.RequestException as e:
            raise ValueError(f"Request error: {e}")

    def get_account_cash(self) -> dict:
        """
        Fetch account cash balance details.

        Calls GET /equity/account/cash

        Returns:
            Dict with cash balance details including free, total, invested, etc.
        """
        try:
            response = self._request("GET", "equity/account/cash")
            return {
                "free_cash": response.get("free", 0),
                "total_cash": response.get("total", 0),
                "invested_cash": response.get("invested", 0),
                "blocked_cash": response.get("blocked", 0),
                "profit_loss": response.get("ppl", 0),
                "result": response.get("result", 0),
                "raw_response": response,
            }
        except ValueError as e:
            if "401" in str(e):
                print("\n⚠️  401 Authentication Error — Known T212 Beta Issue")
                print("   This is a known issue some users report on demo accounts.")
                print("   Check that your API key was generated in Practice/Demo mode,")
                print("   not in Live mode. Demo keys only work with demo endpoints.")
                raise
            raise

    def get_account_info(self) -> dict:
        """
        Fetch account information (ID, currency, etc).

        Calls GET /equity/account/info

        Returns:
            Dict with id (account ID), currencyCode, and other account metadata
        """
        response = self._request("GET", "equity/account/info")
        return {
            "account_id": response.get("id"),
            "currency": response.get("currencyCode", "GBP"),
            "raw_response": response,
        }

    def get_current_positions(self) -> dict[str, dict]:
        """
        Fetch current open positions.

        Calls GET /equity/portfolio

        Returns:
            Dict of {ticker: {quantity, current_value, current_price, avg_price}}
        """
        response = self._request("GET", "equity/portfolio")

        positions = {}

        # Handle if response is a list or dict
        position_list = response if isinstance(response, list) else response.get("positions", [])

        for position in position_list:
            ticker = position.get("ticker")
            if ticker:
                positions[ticker] = {
                    "quantity": float(position.get("quantity", 0)),
                    "current_price": float(position.get("currentPrice", 0)),
                    "current_value": float(position.get("currentPrice", 0)) * float(position.get("quantity", 0)),
                    "avg_price": float(position.get("averagePrice", 0)),
                    "raw_position": position,
                }

        return positions

    def get_instruments(self) -> list[dict]:
        """
        Fetch the full list of instruments T212 supports.

        Calls GET /equity/metadata/instruments

        Returns:
            List of instrument dicts with ticker, isin, name, currency, etc.
        """
        response = self._request("GET", "equity/metadata/instruments")

        # Handle if response is a list or dict with instruments key
        instruments = response if isinstance(response, list) else response.get("instruments", [])

        return instruments

    def calculate_order_quantity(self, amount_gbp: float, current_price_gbp: float, precision: int = 4) -> float:
        """
        Calculate order quantity from a GBP amount and current price.

        Args:
            amount_gbp: Amount in GBP to spend
            current_price_gbp: Current price per share in GBP
            precision: Number of decimal places to round to (default 4, varies by instrument)

        Returns:
            Quantity rounded to specified decimal places

        Raises:
            ValueError: If price is zero or negative
        """
        if current_price_gbp <= 0:
            raise ValueError(
                f"Invalid price: {current_price_gbp}. "
                f"Price must be positive (got {current_price_gbp})"
            )

        quantity = amount_gbp / current_price_gbp

        # Round to the specified precision
        quantity = round(quantity, precision)

        return quantity

    def get_pending_orders(self) -> list[dict]:
        """
        Fetch all currently active/unfilled orders.

        Calls GET /equity/orders

        Returns:
            List of order dicts with id, ticker, quantity, status, etc.
        """
        response = self._request("GET", "equity/orders")

        # Handle if response is a list or dict with orders key
        orders = response if isinstance(response, list) else response.get("orders", [])

        return orders

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """
        Place a market order for a given ticker and quantity.

        CRITICAL: This endpoint is NOT idempotent per T212's docs.
        Do NOT implement retry logic — retrying could create duplicate orders.
        Each call is a fresh order attempt.

        Args:
            ticker: T212 ticker (e.g., "IGLS_EQ", not "IGLS.L")
            quantity: Quantity to buy (can be fractional)

        Returns:
            T212's raw response as dict. If an error occurred, the dict will have
            an "error" key with T212's error message. No exception is raised;
            the caller is responsible for handling errors gracefully.
        """
        try:
            response = self._request(
                "POST",
                "equity/orders/market",
                json={"ticker": ticker, "quantity": quantity},
            )

            # Success response
            return response

        except ValueError as e:
            # _request raised an error, return it as an error dict
            error_message = str(e)
            return {"error": error_message, "ticker": ticker, "quantity": quantity}

    def get_exchanges(self) -> list[dict]:
        """
        Fetch the list of available exchanges.

        Calls GET /equity/metadata/exchanges

        Returns:
            List of exchange dicts with id, name, code, etc.
        """
        try:
            response = self._request("GET", "equity/metadata/exchanges")
            # Handle if response is a list or dict with exchanges key
            exchanges = response if isinstance(response, list) else response.get("exchanges", [])
            return exchanges
        except Exception as e:
            print(f"⚠️  Note: /equity/metadata/exchanges endpoint not available: {e}")
            return []
