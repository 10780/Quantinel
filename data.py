"""
LAYER 1 · DATA   (owner: Rahul)

MockDataSource generates synthetic-but-realistic, *correlated* daily OHLCV for
NVDA and GOOG, so the rest of the pipeline can be built and tested before any
real API (Alpaca/Polygon) is wired in.

How it works
------------
1. Build a Cholesky-correlated pair of daily log-return streams
   (annual drift & vol per ticker, configurable cross-correlation).
2. Integrate returns into a close-price path via ``cumprod``.
3. Derive open / high / low / volume from the close with small
   random perturbations so every bar looks plausible on a chart.

To go live later: write an ``AlpacaDataSource`` with the same ``load()``
method that returns a ``MarketData``. Nothing downstream changes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from contracts import AssetClass, MarketData


# ============================================================================
# PRIMARY DATA SOURCE — synthetic correlated OHLCV
# ============================================================================

class MockDataSource:
    """Implements DataSource:  load() -> MarketData.

    Parameters
    ----------
    n_days : int
        Number of business days of history to generate (default ~2 years).
    seed : int
        Random seed for full reproducibility.
    corr : float
        Pairwise return correlation between the two tickers (0–1).
    params : dict | None
        Per-ticker generation parameters::

            {ticker: (annual_drift, annual_vol, start_price), ...}

        Defaults to NVDA (high drift/vol) and GOOG (moderate drift/vol).
    """

    def __init__(
        self,
        n_days: int = 504,
        seed: int = 7,
        corr: float = 0.6,
        params: dict | None = None,
    ):
        self.n_days = n_days
        self.seed = seed
        self.corr = corr
        # (annual drift, annual vol, start price)
        self.params = params or {
            "NVDA": (0.35, 0.45, 480.0),
            "GOOG": (0.15, 0.28, 140.0),
        }

    def load(self) -> MarketData:
        """Generate synthetic OHLCV bars and return a ``MarketData`` contract.

        Returns
        -------
        MarketData
            Frozen dataclass with ``tickers``, ``bars``, and helpers
            ``close_prices()``, ``returns()``, ``slice_until(as_of)``.
        """
        rng = np.random.default_rng(self.seed)
        tickers = list(self.params)

        # --- date axis --------------------------------------------------
        dates = pd.bdate_range(
            end=pd.Timestamp.today().normalize(), periods=self.n_days
        )
        n = len(dates)  # source of truth for array length

        # --- correlated daily returns ------------------------------------
        n_assets = len(tickers)
        corr_matrix = np.full((n_assets, n_assets), self.corr)
        np.fill_diagonal(corr_matrix, 1.0)
        L = np.linalg.cholesky(corr_matrix)  # lower-triangular factor

        mu = np.array([self.params[t][0] for t in tickers]) / 252
        sig = np.array([self.params[t][1] for t in tickers]) / np.sqrt(252)

        z = rng.standard_normal((n, n_assets))
        daily_returns = mu + sig * (z @ L.T)  # correlated daily returns

        # --- build OHLCV per ticker -------------------------------------
        bars: dict[str, pd.DataFrame] = {}
        for i, t in enumerate(tickers):
            close = self.params[t][2] * np.cumprod(1 + daily_returns[:, i])

            # open ≈ previous close with tiny overnight gap noise
            open_ = np.concatenate([[close[0]], close[:-1]]) * (
                1 + rng.normal(0, 0.001, n)
            )
            # high >= max(open, close), low <= min(open, close)
            high = np.maximum(open_, close) * (
                1 + np.abs(rng.normal(0, 0.004, n))
            )
            low = np.minimum(open_, close) * (
                1 - np.abs(rng.normal(0, 0.004, n))
            )
            volume = rng.integers(2_000_000, 8_000_000, n).astype(float)

            bars[t] = pd.DataFrame(
                {
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                },
                index=dates,
            )

        return MarketData(tickers=tickers, bars=bars)


# ============================================================================
# LIVE DATA SOURCE — real OHLCV via yfinance
# ============================================================================

class YFinanceDataSource:
    """Implements DataSource: load() -> MarketData.

    Fetches real daily OHLCV from Yahoo Finance for the given tickers and
    date range. Drop-in replacement for MockDataSource — same contract, no
    downstream changes needed.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols, e.g. ["NVDA", "GOOG"].
    start : str
        Start date in "YYYY-MM-DD" format.
    end : str | None
        End date (exclusive). Defaults to today.
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        start: str = "2023-01-01",
        end: str | None = None,
    ):
        self.tickers = tickers or ["NVDA", "GOOG"]
        self.start = start
        self.end = end or pd.Timestamp.today().strftime("%Y-%m-%d")

    def load(self) -> MarketData:
        import yfinance as yf

        raw = yf.download(
            self.tickers,
            start=self.start,
            end=self.end,
            auto_adjust=True,
            progress=False,
        )

        # yfinance returns a MultiIndex frame when multiple tickers are given;
        # a flat frame when only one ticker is given. Normalise to flat per ticker.
        if isinstance(raw.columns, pd.MultiIndex):
            frames = {t: raw.xs(t, axis=1, level=1).dropna() for t in self.tickers}
        else:
            frames = {self.tickers[0]: raw.dropna()}

        bars: dict[str, pd.DataFrame] = {}
        for ticker, df in frames.items():
            df = df.rename(columns=str.lower)
            bars[ticker] = df[["open", "high", "low", "close", "volume"]]

        return MarketData(tickers=self.tickers, bars=bars)


# ============================================================================
# TINY STATIC DATA SOURCE — deterministic 5-row dataset for unit tests
# ============================================================================

class TinyMockDataSource:
    """A hard-coded, deterministic 5-day dataset for fast unit tests.

    No randomness, no Cholesky — just two tickers with hand-picked OHLCV
    so that expected values (close_prices, returns, slice_until) can be
    asserted by hand.

    Also provides ``load_news()`` for mock news/world-event data that
    downstream layers (e.g. sentiment-aware forecasters) can consume.
    """

    def load(self) -> MarketData:
        dates = pd.bdate_range("2025-01-06", periods=5, freq="B")
        bars: dict[str, pd.DataFrame] = {
            "AAA": pd.DataFrame(
                {
                    "open":   [100.0, 102.0, 101.0, 105.0, 103.0],
                    "high":   [103.0, 104.0, 106.0, 107.0, 108.0],
                    "low":    [ 99.0, 100.0,  99.0, 103.0, 101.0],
                    "close":  [102.0, 101.0, 105.0, 103.0, 107.0],
                    "volume": [1e6,   1.2e6, 0.8e6, 1.5e6, 1.1e6],
                },
                index=dates,
            ),
            "BBB": pd.DataFrame(
                {
                    "open":   [50.0, 51.0, 52.0, 50.0, 53.0],
                    "high":   [52.0, 53.0, 54.0, 53.0, 55.0],
                    "low":    [49.0, 50.0, 50.0, 49.0, 51.0],
                    "close":  [51.0, 52.0, 50.0, 53.0, 54.0],
                    "volume": [5e5,  6e5,  4e5,  7e5,  5.5e5],
                },
                index=dates,
            ),
        }
        return MarketData(tickers=["AAA", "BBB"], bars=bars)

    @staticmethod
    def load_news() -> list[dict]:
        """Return deterministic mock news / world-event data.

        Each item is a JSON-serializable dict with:
            - ``date``       : ISO-8601 date string (aligned with OHLCV dates)
            - ``ticker``     : affected ticker, or ``"MACRO"`` for broad events
            - ``headline``   : human-readable headline
            - ``source``     : news outlet name
            - ``sentiment``  : float in [-1.0, +1.0]  (neg = bearish, pos = bullish)
            - ``category``   : ``"earnings"`` | ``"macro"`` | ``"product"`` | ``"analyst"`` | ``"geopolitical"``
            - ``relevance``  : float in [0.0, 1.0]  (how relevant to the ticker)

        Headlines are chosen to logically match the hand-picked price
        movements so tests can assert sentiment ↔ return correlation.

        Returns
        -------
        list[dict]
            A list of 10 news items spanning the 5 trading days.
        """
        return [
            # ── Day 1  (2025-01-06) ── AAA +2%, BBB +2%  → bullish news
            {
                "date": "2025-01-06",
                "ticker": "AAA",
                "headline": "AAA Corp beats Q4 earnings estimates by 12%",
                "source": "Reuters",
                "sentiment": 0.85,
                "category": "earnings",
                "relevance": 0.95,
            },
            {
                "date": "2025-01-06",
                "ticker": "MACRO",
                "headline": "Fed signals potential rate cuts in H1 2025",
                "source": "Bloomberg",
                "sentiment": 0.60,
                "category": "macro",
                "relevance": 0.70,
            },
            # ── Day 2  (2025-01-07) ── AAA −1%, BBB +2%  → mixed
            {
                "date": "2025-01-07",
                "ticker": "AAA",
                "headline": "Analysts downgrade AAA citing valuation concerns",
                "source": "CNBC",
                "sentiment": -0.45,
                "category": "analyst",
                "relevance": 0.80,
            },
            {
                "date": "2025-01-07",
                "ticker": "BBB",
                "headline": "BBB Inc announces new product line, shares rise",
                "source": "MarketWatch",
                "sentiment": 0.70,
                "category": "product",
                "relevance": 0.90,
            },
            # ── Day 3  (2025-01-08) ── AAA +4%, BBB −3.8%  → divergence
            {
                "date": "2025-01-08",
                "ticker": "AAA",
                "headline": "AAA secures $2B government contract for AI infrastructure",
                "source": "WSJ",
                "sentiment": 0.90,
                "category": "product",
                "relevance": 0.95,
            },
            {
                "date": "2025-01-08",
                "ticker": "BBB",
                "headline": "BBB faces supply chain disruptions in Asia-Pacific",
                "source": "Financial Times",
                "sentiment": -0.65,
                "category": "geopolitical",
                "relevance": 0.85,
            },
            # ── Day 4  (2025-01-09) ── AAA −1.9%, BBB +6%  → reversal
            {
                "date": "2025-01-09",
                "ticker": "AAA",
                "headline": "AAA CFO sells $15M in insider shares",
                "source": "SEC Filing",
                "sentiment": -0.40,
                "category": "earnings",
                "relevance": 0.75,
            },
            {
                "date": "2025-01-09",
                "ticker": "BBB",
                "headline": "BBB resolves supply issues; analyst upgrades to Buy",
                "source": "Goldman Sachs",
                "sentiment": 0.80,
                "category": "analyst",
                "relevance": 0.90,
            },
            # ── Day 5  (2025-01-10) ── AAA +3.9%, BBB +1.9%  → broad rally
            {
                "date": "2025-01-10",
                "ticker": "MACRO",
                "headline": "US jobs report beats expectations; markets rally broadly",
                "source": "Bloomberg",
                "sentiment": 0.75,
                "category": "macro",
                "relevance": 0.80,
            },
            {
                "date": "2025-01-10",
                "ticker": "AAA",
                "headline": "AAA launches next-gen chip platform at CES 2025",
                "source": "The Verge",
                "sentiment": 0.65,
                "category": "product",
                "relevance": 0.85,
            },
        ]


# ============================================================================
# COMMODITY DATA SOURCES
# ============================================================================

# Friendly display name -> Yahoo Finance futures symbol
COMMODITY_YFINANCE_SYMBOLS: dict[str, str] = {
    "GOLD":      "GC=F",
    "SILVER":    "SI=F",
    "PLATINUM":  "PL=F",
    "PALLADIUM": "PA=F",
    "OIL":       "CL=F",
}

# Realistic synthetic parameters: (annual_drift, annual_vol, start_price)
_COMMODITY_MOCK_PARAMS: dict[str, tuple[float, float, float]] = {
    "GOLD":      (0.07, 0.15, 2000.0),
    "SILVER":    (0.05, 0.25,   25.0),
    "PLATINUM":  (0.03, 0.22,  950.0),
    "PALLADIUM": (0.04, 0.35, 1000.0),
    "OIL":       (0.05, 0.30,   75.0),
}

# Intra-group correlation matrix rows/cols ordered as the keys above.
# Precious metals (first 4) are highly correlated; OIL has weaker linkage.
_COMMODITY_CORR: np.ndarray = np.array([
    #  GOLD  SILV  PLAT  PALL   OIL
    [1.00, 0.60, 0.60, 0.55, 0.20],  # GOLD
    [0.60, 1.00, 0.55, 0.50, 0.20],  # SILVER
    [0.60, 0.55, 1.00, 0.55, 0.15],  # PLATINUM
    [0.55, 0.50, 0.55, 1.00, 0.15],  # PALLADIUM
    [0.20, 0.20, 0.15, 0.15, 1.00],  # OIL
])


class CommodityDataSource:
    """Fetches commodity futures data via yfinance.

    Uses human-readable ticker names (``GOLD``, ``SILVER``, ``PLATINUM``,
    ``PALLADIUM``, ``OIL``) and maps them internally to the correct Yahoo
    Finance futures symbols (``GC=F``, ``SI=F``, ``PL=F``, ``PA=F``,
    ``CL=F``).  Returns a ``MarketData`` with ``asset_classes`` set to
    ``AssetClass.COMMODITY`` for every ticker.

    Parameters
    ----------
    commodities : list[str] | None
        Subset of ``COMMODITY_YFINANCE_SYMBOLS`` keys to load.
        Defaults to all five (GOLD, SILVER, PLATINUM, PALLADIUM, OIL).
    start : str
        Start date in ``YYYY-MM-DD`` format.
    end : str | None
        End date (exclusive). Defaults to today.
    """

    DEFAULT_COMMODITIES: list[str] = list(COMMODITY_YFINANCE_SYMBOLS)

    def __init__(
        self,
        commodities: list[str] | None = None,
        start: str = "2023-01-01",
        end: str | None = None,
    ) -> None:
        self.commodities = commodities or self.DEFAULT_COMMODITIES
        self.start = start
        self.end = end or pd.Timestamp.today().strftime("%Y-%m-%d")

    def load(self) -> MarketData:
        """Download commodity futures and return a ``MarketData`` contract."""
        import yfinance as yf

        yf_symbols = [COMMODITY_YFINANCE_SYMBOLS[c] for c in self.commodities]

        raw = yf.download(
            yf_symbols,
            start=self.start,
            end=self.end,
            auto_adjust=True,
            progress=False,
        )

        bars: dict[str, pd.DataFrame] = {}
        for name, symbol in zip(self.commodities, yf_symbols):
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(symbol, axis=1, level=1).dropna()
            else:
                df = raw.dropna()
            df = df.rename(columns=str.lower)
            bars[name] = df[["open", "high", "low", "close", "volume"]]

        asset_classes = {c: AssetClass.COMMODITY for c in self.commodities}
        return MarketData(
            tickers=self.commodities,
            bars=bars,
            asset_classes=asset_classes,
        )


class MockCommodityDataSource:
    """Synthetic commodity data with realistic block-correlation structure.

    Generates correlated daily OHLCV for the five standard commodities
    (GOLD, SILVER, PLATINUM, PALLADIUM, OIL) using the same Cholesky
    construction as ``MockDataSource``.  The precious-metals group is
    internally correlated (~0.55–0.60); OIL is only weakly linked (~0.15–0.20).

    Parameters
    ----------
    commodities : list[str] | None
        Subset of the five commodities to generate.  Defaults to all five.
    n_days : int
        Number of business days of history (default 504 ≈ 2 years).
    seed : int
        Random seed for reproducibility.
    """

    DEFAULT_COMMODITIES: list[str] = list(_COMMODITY_MOCK_PARAMS)

    def __init__(
        self,
        commodities: list[str] | None = None,
        n_days: int = 504,
        seed: int = 42,
    ) -> None:
        self.commodities = commodities or self.DEFAULT_COMMODITIES
        self.n_days = n_days
        self.seed = seed

    def load(self) -> MarketData:
        """Generate synthetic commodity OHLCV and return a ``MarketData`` contract."""
        rng = np.random.default_rng(self.seed)

        all_names = list(_COMMODITY_MOCK_PARAMS)
        indices = [all_names.index(c) for c in self.commodities]

        # Sub-select correlation block for requested commodities
        corr = _COMMODITY_CORR[np.ix_(indices, indices)].copy()
        L = np.linalg.cholesky(corr)

        dates = pd.bdate_range(
            end=pd.Timestamp.today().normalize(), periods=self.n_days
        )
        n = len(dates)
        n_assets = len(self.commodities)

        mu  = np.array([_COMMODITY_MOCK_PARAMS[c][0] for c in self.commodities]) / 252
        sig = np.array([_COMMODITY_MOCK_PARAMS[c][1] for c in self.commodities]) / np.sqrt(252)

        z = rng.standard_normal((n, n_assets))
        daily_returns = mu + sig * (z @ L.T)

        bars: dict[str, pd.DataFrame] = {}
        for i, name in enumerate(self.commodities):
            start_price = _COMMODITY_MOCK_PARAMS[name][2]
            close = start_price * np.cumprod(1 + daily_returns[:, i])

            open_ = np.concatenate([[close[0]], close[:-1]]) * (
                1 + rng.normal(0, 0.001, n)
            )
            high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
            low  = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
            # Commodity futures volume is contracts traded — use smaller scale
            volume = rng.integers(10_000, 200_000, n).astype(float)

            bars[name] = pd.DataFrame(
                {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
                index=dates,
            )

        asset_classes = {c: AssetClass.COMMODITY for c in self.commodities}
        return MarketData(
            tickers=self.commodities,
            bars=bars,
            asset_classes=asset_classes,
        )


class CombinedDataSource:
    """Merges two or more asset-class data sources into one ``MarketData``.

    All downstream layers (Forecast, Risk, Optimizer) are ticker-agnostic and
    work unchanged on the combined universe.  The ``asset_classes`` field on the
    returned ``MarketData`` lets any layer filter by asset type when needed.

    Accepts any number of positional source arguments, each with a
    ``load() -> MarketData`` method.  Bars are aligned to the intersection of
    business days across all sources.

    Parameters
    ----------
    *sources : DataSource
        Two or more data sources.  Examples::

            CombinedDataSource(MockDataSource(), MockCommodityDataSource())
            CombinedDataSource(MockDataSource(), MockCommodityDataSource(),
                               MockHousingDataSource())
    """

    def __init__(self, *sources) -> None:
        if len(sources) < 2:
            raise ValueError("CombinedDataSource requires at least two sources.")
        self.sources = sources

    def load(self) -> MarketData:
        """Load all sources and merge into a single ``MarketData`` contract."""
        loaded = [s.load() for s in self.sources]

        # Align date indices: intersection across all sources
        common = loaded[0].close_prices().index
        for md in loaded[1:]:
            common = common.intersection(md.close_prices().index)

        merged_bars: dict[str, pd.DataFrame] = {}
        combined_tickers: list[str] = []
        combined_classes: dict[str, AssetClass] = {}

        for md in loaded:
            for t in md.tickers:
                merged_bars[t] = md.bars[t].loc[common]
                combined_classes[t] = md.asset_classes.get(t, AssetClass.EQUITY)
            combined_tickers.extend(md.tickers)

        return MarketData(
            tickers=combined_tickers,
            bars=merged_bars,
            asset_classes=combined_classes,
        )


# ============================================================================
# HOUSING / REAL ESTATE DATA SOURCES
# ============================================================================

# Friendly display name -> Yahoo Finance ticker (ETFs, not futures)
HOUSING_YFINANCE_SYMBOLS: dict[str, str] = {
    "HOUSING":      "VNQ",   # Vanguard Real Estate ETF — broad REIT index
    "HOMEBUILDERS": "ITB",   # iShares U.S. Home Construction ETF
    "MORTGAGES":    "REM",   # iShares Mortgage Real Estate ETF
    "COMMERCIAL_RE":"IYR",   # iShares U.S. Real Estate ETF (commercial)
    "RESIDENTIAL":  "REZ",   # iShares Residential & Multisector Real Estate ETF
}

# Realistic synthetic parameters: (annual_drift, annual_vol, start_price)
_HOUSING_MOCK_PARAMS: dict[str, tuple[float, float, float]] = {
    "HOUSING":       (0.08, 0.18,  90.0),
    "HOMEBUILDERS":  (0.12, 0.28,  85.0),
    "MORTGAGES":     (0.06, 0.22,  25.0),
    "COMMERCIAL_RE": (0.07, 0.20,  95.0),
    "RESIDENTIAL":   (0.08, 0.19,  80.0),
}

# Intra-group correlation matrix — rows/cols ordered as the keys above.
# Broad REIT ETFs are highly correlated; homebuilders and mortgage REITs
# are more idiosyncratic but still linked.
_HOUSING_CORR: np.ndarray = np.array([
    #  HOUS  HOME  MORT  COMM  RESI
    [1.00, 0.65, 0.55, 0.78, 0.80],  # HOUSING
    [0.65, 1.00, 0.40, 0.60, 0.65],  # HOMEBUILDERS
    [0.55, 0.40, 1.00, 0.55, 0.58],  # MORTGAGES
    [0.78, 0.60, 0.55, 1.00, 0.72],  # COMMERCIAL_RE
    [0.80, 0.65, 0.58, 0.72, 1.00],  # RESIDENTIAL
])


class HousingDataSource:
    """Fetches housing / real estate ETF data via yfinance.

    Uses human-readable names (``HOUSING``, ``HOMEBUILDERS``, ``MORTGAGES``,
    ``COMMERCIAL_RE``, ``RESIDENTIAL``) mapped internally to liquid ETF tickers
    (``VNQ``, ``ITB``, ``REM``, ``IYR``, ``REZ``).  Returns a ``MarketData``
    with ``asset_classes`` set to ``AssetClass.REAL_ESTATE`` for every ticker.

    Parameters
    ----------
    instruments : list[str] | None
        Subset of ``HOUSING_YFINANCE_SYMBOLS`` keys to load.
        Defaults to all five.
    start : str
        Start date in ``YYYY-MM-DD`` format.
    end : str | None
        End date (exclusive). Defaults to today.
    """

    DEFAULT_INSTRUMENTS: list[str] = list(HOUSING_YFINANCE_SYMBOLS)

    def __init__(
        self,
        instruments: list[str] | None = None,
        start: str = "2023-01-01",
        end: str | None = None,
    ) -> None:
        self.instruments = instruments or self.DEFAULT_INSTRUMENTS
        self.start = start
        self.end = end or pd.Timestamp.today().strftime("%Y-%m-%d")

    def load(self) -> MarketData:
        """Download housing ETF data and return a ``MarketData`` contract."""
        import yfinance as yf

        yf_symbols = [HOUSING_YFINANCE_SYMBOLS[i] for i in self.instruments]

        raw = yf.download(
            yf_symbols,
            start=self.start,
            end=self.end,
            auto_adjust=True,
            progress=False,
        )

        bars: dict[str, pd.DataFrame] = {}
        for name, symbol in zip(self.instruments, yf_symbols):
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(symbol, axis=1, level=1).dropna()
            else:
                df = raw.dropna()
            df = df.rename(columns=str.lower)
            bars[name] = df[["open", "high", "low", "close", "volume"]]

        asset_classes = {i: AssetClass.REAL_ESTATE for i in self.instruments}
        return MarketData(
            tickers=self.instruments,
            bars=bars,
            asset_classes=asset_classes,
        )


class MockHousingDataSource:
    """Synthetic housing / real estate data with realistic block-correlation.

    Generates correlated daily OHLCV for the five standard real estate
    instruments using the same Cholesky construction as ``MockDataSource``.
    Broad REIT ETFs are highly correlated (~0.72–0.80); homebuilders and
    mortgage REITs are more idiosyncratic but still linked (~0.40–0.65).

    Parameters
    ----------
    instruments : list[str] | None
        Subset of the five instruments to generate.  Defaults to all five.
    n_days : int
        Number of business days of history (default 504 ≈ 2 years).
    seed : int
        Random seed for reproducibility.
    """

    DEFAULT_INSTRUMENTS: list[str] = list(_HOUSING_MOCK_PARAMS)

    def __init__(
        self,
        instruments: list[str] | None = None,
        n_days: int = 504,
        seed: int = 99,
    ) -> None:
        self.instruments = instruments or self.DEFAULT_INSTRUMENTS
        self.n_days = n_days
        self.seed = seed

    def load(self) -> MarketData:
        """Generate synthetic housing OHLCV and return a ``MarketData`` contract."""
        rng = np.random.default_rng(self.seed)

        all_names = list(_HOUSING_MOCK_PARAMS)
        indices = [all_names.index(i) for i in self.instruments]

        corr = _HOUSING_CORR[np.ix_(indices, indices)].copy()
        L = np.linalg.cholesky(corr)

        dates = pd.bdate_range(
            end=pd.Timestamp.today().normalize(), periods=self.n_days
        )
        n = len(dates)
        n_assets = len(self.instruments)

        mu  = np.array([_HOUSING_MOCK_PARAMS[i][0] for i in self.instruments]) / 252
        sig = np.array([_HOUSING_MOCK_PARAMS[i][1] for i in self.instruments]) / np.sqrt(252)

        z = rng.standard_normal((n, n_assets))
        daily_returns = mu + sig * (z @ L.T)

        bars: dict[str, pd.DataFrame] = {}
        for idx, name in enumerate(self.instruments):
            start_price = _HOUSING_MOCK_PARAMS[name][2]
            close = start_price * np.cumprod(1 + daily_returns[:, idx])

            open_ = np.concatenate([[close[0]], close[:-1]]) * (
                1 + rng.normal(0, 0.001, n)
            )
            high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
            low  = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
            # ETF volume scale (shares traded daily)
            volume = rng.integers(500_000, 5_000_000, n).astype(float)

            bars[name] = pd.DataFrame(
                {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
                index=dates,
            )

        asset_classes = {i: AssetClass.REAL_ESTATE for i in self.instruments}
        return MarketData(
            tickers=self.instruments,
            bars=bars,
            asset_classes=asset_classes,
        )