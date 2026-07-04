from typing import Any, Dict, Optional

import requests


class AlpacaError(RuntimeError):
    pass


class AlpacaRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        trading_base_url: str,
        data_base_url: str,
        data_feed: str,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.trading_base_url = trading_base_url.rstrip("/")
        self.data_base_url = data_base_url.rstrip("/")
        self.data_feed = data_feed
        self.timeout = timeout

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not self.api_key or not self.secret_key:
            raise AlpacaError("Missing Alpaca API credentials")
        url = f"{base_url}{path}"
        response = requests.request(
            method,
            url,
            headers=self.headers,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise AlpacaError(
                f"{method} {path} failed status={response.status_code} body={response.text[:800]}"
            )
        if response.status_code == 204 or not response.text.strip():
            return {"ok": True}
        return response.json()

    def trading(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, self.trading_base_url, path, **kwargs)

    def data(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, self.data_base_url, path, **kwargs)

    def account(self) -> Dict[str, Any]:
        return self.trading("GET", "/v2/account")

    def positions(self) -> Any:
        return self.trading("GET", "/v2/positions")

    def open_orders(self) -> Any:
        return self.trading("GET", "/v2/orders", params={"status": "open", "limit": 500})

    def state(self) -> Dict[str, Any]:
        return {
            "account": self.account(),
            "positions": self.positions(),
            "open_orders": self.open_orders(),
        }

    def portfolio_history(
        self,
        period: str = "1M",
        timeframe: str = "1D",
        intraday_reporting: str = "market_hours",
    ) -> Dict[str, Any]:
        return self.trading(
            "GET",
            "/v2/account/portfolio/history",
            params={
                "period": period,
                "timeframe": timeframe,
                "intraday_reporting": intraday_reporting,
            },
        )

    def latest_quote(self, symbol: str) -> Dict[str, Any]:
        return self.data(
            "GET",
            f"/v2/stocks/{symbol.upper()}/quotes/latest",
            params={"feed": self.data_feed},
        )

    def latest_trade(self, symbol: str) -> Dict[str, Any]:
        return self.data(
            "GET",
            f"/v2/stocks/{symbol.upper()}/trades/latest",
            params={"feed": self.data_feed},
        )

    def stock_bars(
        self,
        symbols: list[str],
        *,
        start: str,
        end: Optional[str] = None,
        timeframe: str = "1Day",
        limit: int = 1000,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "symbols": ",".join(symbol.upper() for symbol in symbols),
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            "adjustment": "raw",
            "feed": self.data_feed,
        }
        if end:
            params["end"] = end
        return self.data("GET", "/v2/stocks/bars", params=params)

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Optional[float] = None,
        notional: Optional[float] = None,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        extended_hours: bool = False,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force.lower(),
        }
        if qty is not None:
            body["qty"] = str(qty)
        if notional is not None:
            body["notional"] = str(notional)
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if extended_hours:
            body["extended_hours"] = True
        return self.trading("POST", "/v2/orders", json_body=body)

    def cancel_all_orders(self) -> Any:
        return self.trading("DELETE", "/v2/orders")

    def close_all_positions(self, cancel_orders: bool = True) -> Any:
        return self.trading(
            "DELETE",
            "/v2/positions",
            params={"cancel_orders": str(cancel_orders).lower()},
        )
