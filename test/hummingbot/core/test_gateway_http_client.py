import asyncio
import sys
import types
from decimal import Decimal
from unittest import TestCase

from hummingbot.client.settings import AllConnectorSettings

sys.modules.setdefault("aioprocessing", types.SimpleNamespace(AioConnection=object))

from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient


class GatewayHttpClientTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._original_connector_settings = dict(AllConnectorSettings.get_connector_settings())
        GatewayHttpClient._GatewayHttpClient__instance = None
        self.client = GatewayHttpClient()

    def tearDown(self) -> None:
        AllConnectorSettings.all_connector_settings = self._original_connector_settings
        GatewayHttpClient._GatewayHttpClient__instance = None
        super().tearDown()

    def test_register_gateway_connectors_uses_zero_percent_fees(self):
        connector_name = "uniswap/clmm"
        AllConnectorSettings.all_connector_settings.pop(connector_name, None)

        asyncio.run(self.client._register_gateway_connectors([connector_name]))

        connector_settings = AllConnectorSettings.get_connector_settings()[connector_name]

        self.assertEqual(Decimal("0"), connector_settings.trade_fee_schema.maker_percent_fee_decimal)
        self.assertEqual(Decimal("0"), connector_settings.trade_fee_schema.taker_percent_fee_decimal)
