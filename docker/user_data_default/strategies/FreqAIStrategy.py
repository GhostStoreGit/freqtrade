import logging
from functools import reduce

import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.strategy import IStrategy


logger = logging.getLogger(__name__)


class FreqAIStrategy(IStrategy):
    """
    Estrategia FreqAI com LightGBM para predicao de preco.
    Usa indicadores tecnicos como features e treina modelos de ML
    que sao retreinados automaticamente a cada 24h.

    Baseada na FreqaiExampleStrategy oficial, com ajustes para:
    - Apenas long (spot trading, sem short)
    - Trailing stop para proteger lucros
    - Thresholds mais conservadores para entrada/saida

    Modo: dry-run (simulacao) -> depois live
    """

    minimal_roi = {
        "0": 0.1,      # 10% de lucro minimo
        "30": 0.05,     # 5% apos 30 minutos
        "60": 0.02,     # 2% apos 60 minutos
        "120": 0.01,    # 1% apos 120 minutos
    }

    stoploss = -0.05  # Stop loss de 5%

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    timeframe = "5m"

    process_only_new_candles = True
    use_exit_signal = True
    can_short = False  # Apenas long (spot)

    # FreqAI precisa de candles de startup para calcular indicadores
    startup_candle_count: int = 40

    plot_config = {
        "main_plot": {},
        "subplots": {
            "&-s_close": {"&-s_close": {"color": "blue"}},
            "do_predict": {
                "do_predict": {"color": "brown"},
            },
        },
    }

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        """
        Features que expandem automaticamente com base em:
        - indicator_periods_candles (10, 20, 40)
        - include_timeframes (5m, 15m, 1h)
        - include_shifted_candles (2)
        - include_corr_pairlist (BTC, ETH, SOL)
        """

        # Indicadores de momentum
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)

        # Medias moveis
        dataframe["%-sma-period"] = ta.SMA(dataframe, timeperiod=period)
        dataframe["%-ema-period"] = ta.EMA(dataframe, timeperiod=period)

        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=period, stds=2.2
        )
        dataframe["bb_lowerband-period"] = bollinger["lower"]
        dataframe["bb_middleband-period"] = bollinger["mid"]
        dataframe["bb_upperband-period"] = bollinger["upper"]

        dataframe["%-bb_width-period"] = (
            dataframe["bb_upperband-period"] - dataframe["bb_lowerband-period"]
        ) / dataframe["bb_middleband-period"]

        dataframe["%-close-bb_lower-period"] = (
            dataframe["close"] / dataframe["bb_lowerband-period"]
        )

        # Rate of Change
        dataframe["%-roc-period"] = ta.ROC(dataframe, timeperiod=period)

        # Volume relativo
        dataframe["%-relative_volume-period"] = (
            dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        )

        return dataframe

    def feature_engineering_expand_basic(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """
        Features basicas expandidas por timeframe e shifted candles,
        mas NAO por indicator_periods_candles.
        """
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-raw_volume"] = dataframe["volume"]
        dataframe["%-raw_price"] = dataframe["close"]

        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """
        Features que NAO sao expandidas automaticamente.
        """
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour

        return dataframe

    def set_freqai_targets(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """
        Define os targets (labels) que o modelo vai prever.
        O prefixo &- e obrigatorio para FreqAI reconhecer.

        Target: media movel futura do preco relativa ao preco atual.
        Se > 0, preco deve subir. Se < 0, preco deve cair.
        """
        dataframe["&-s_close"] = (
            dataframe["close"]
            .shift(-self.freqai_info["feature_parameters"]["label_period_candles"])
            .rolling(self.freqai_info["feature_parameters"]["label_period_candles"])
            .mean()
            / dataframe["close"]
            - 1
        )

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Chamado pelo Freqtrade. Inicia o pipeline do FreqAI.
        """
        dataframe = self.freqai.start(dataframe, metadata, self)

        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        """
        Regras de ENTRADA (compra).
        Usa a predicao do modelo (&-s_close) e o indicador de confianca (do_predict).
        """
        enter_long_conditions = [
            df["do_predict"] == 1,          # Modelo confia na predicao
            df["&-s_close"] > 0.01,         # Modelo preve alta > 1%
        ]

        if enter_long_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_long_conditions),
                ["enter_long", "enter_tag"]
            ] = (1, "long")

        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        """
        Regras de SAIDA (venda).
        Sai quando o modelo preve queda ou perde confianca.
        """
        exit_long_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] < 0,
        ]

        if exit_long_conditions:
            df.loc[
                reduce(lambda x, y: x & y, exit_long_conditions), "exit_long"
            ] = 1

        return df

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        entry_tag,
        side: str,
        **kwargs,
    ) -> bool:
        """
        Confirmacao extra: rejeita entrada se preco subiu mais de 0.25%
        desde o ultimo candle (evita comprar em spike).
        """
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = df.iloc[-1].squeeze()

        if rate > (last_candle["close"] * (1 + 0.0025)):
            return False

        return True
