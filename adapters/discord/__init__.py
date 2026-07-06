# -*- coding: utf-8 -*-
"""adapters.discord — Discord IO 어댑터 (컨트롤·HITL·알림 접점).

'2중 결합'(YouTube 출력 / Discord IO) 중 Discord 면. 현재는 채널 정체성 매핑(경계 분리)까지
담고, 실제 봇 런타임(discord_bot·notifier·approvals)은 아직 이식 전이다.
"""
