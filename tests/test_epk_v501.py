"""ЕПК через Direct API v501 (2026-11-11): в v5 Click.ru-прокси ответил
«unknown parameter UnifiedCampaign» — кампания, группа UnifiedAdGroup,
фразы и объявления ЕПК идут в v501, обычные кампании остаются в v5."""
from __future__ import annotations

import asyncio

from app.click_client import ClickRUClient


def test_epk_calls_use_v501_and_unified_group():
    calls = []

    async def fake_direct_api(self, client_login, method_path, body, api_version="v5", accept_language=None):
        calls.append((method_path, api_version, body))
        return {"result": {"AddResults": [{"Id": 777}]}}

    ClickRUClient.direct_api, orig = fake_direct_api, ClickRUClient.direct_api
    try:
        c = ClickRUClient(token="t")

        async def run():
            await c.create_draft_unified_campaign("porg-1", "Санта", "2026-11-11", target_drr=30, goal_id=5, counter_id=99)
            await c.create_adgroup("porg-1", 777, "Санта", [225], unified=True)
            await c.add_keywords("porg-1", 777, ["санта"], api_version="v501")
            await c.create_text_ad("porg-1", 777, "Заголовок", "Текст", "https://x.ru", api_version="v501")
            await c.create_adgroup("porg-1", 777, "Обычная", [225])

        asyncio.run(run())
    finally:
        ClickRUClient.direct_api = orig

    camp, group, kw, ad, plain = calls
    assert camp[0] == "campaigns" and camp[1] == "v501"
    uc = camp[2]["params"]["Campaigns"][0]["UnifiedCampaign"]
    assert uc["CounterIds"] == {"Items": [99]} and uc["BiddingStrategy"]["Search"]["AverageCrr"] == {"Crr": 30, "GoalId": 5}
    assert uc["BiddingStrategy"]["Network"] == {"BiddingStrategyType": "NETWORK_DEFAULT"}
    assert group[1] == "v501" and group[2]["params"]["AdGroups"][0]["UnifiedAdGroup"] == {"OfferRetargeting": "NO"}
    assert kw[1] == "v501" and ad[1] == "v501"
    assert plain[1] == "v5" and "UnifiedAdGroup" not in plain[2]["params"]["AdGroups"][0]
