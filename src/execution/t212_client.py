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

    def get_account_summary(self) -> dict:
        """
        Fetch account summary (balance, currency, available cash).

        Returns:
            Dict with keys: balance, currency, cash, account_id, etc.
        """
        response = self._request("GET", "account/cash")

        return {
            "balance": response.get("investedCash", 0) + response.get("freeCash", 0),
            "invested_cash": response.get("investedCash", 0),
            "free_cash": response.get("freeCash", 0),
            "currency": response.get("currency", "GBP"),
            "cash": response.get("freeCash", 0),
            "raw_response": response,
        }

    def get_current_positions(self) -> dict[str, dict]:
        """
        Fetch current open positions.

        Returns:
            Dict of {ticker: {quantity, current_value, avg_price}}
        """
        response = self._request("GET", "portfolio/open-positions")

        positions = {}
        for position in response if isinstance(response, list) else response.get("positions", []):
            ticker = position.get("ticker")
            if ticker:
                positions[ticker] = {
                    "quantity": float(position.get("quantity", 0)),
                    "current_value": float(position.get("currentPrice", 0)) * float(position.get("quantity", 0)),
                    "current_price": float(position.get("currentPrice", 0)),
                    "avg_price": float(position.get("averagePrice", 0)),
                    "raw_position": position,
                }

        return positions
