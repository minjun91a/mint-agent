"""제조 초안의 명시적인 수량·날짜 주장 검사. 해석하지 못한 숫자는 사람에게 보낸다.

숫자의 집합 포함 여부가 아니라 (자재, 수량 항목) 또는 날짜 역할을 대조한다.
자유 문장 전체의 의미/부정/인과를 판정하는 자연어 이해기는 아니다.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

from adapters.manufacturing.analyze import CascadeImpact
from core.trust.verify import Violation

CODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,4}-\d+(?![A-Za-z0-9])")
_DATE = re.compile(r"(?<!\d)(\d{4})\s*(?:[-/.]|년\s*)(\d{1,2})\s*(?:[-/.]|월\s*)(\d{1,2})(?:\s*일)?(?!\d)")
_QUANTITY = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?\s*(개|EA|일)", re.I)
_BOUNDARY = re.compile(r"[\n;!?。]|(?<!\d)\.(?!\d)")
_Q_ROLE = re.compile(r"출하\s*(?:수량|량)|미완성|남은\s*생산|부족|추가|필요|소요|확보|가용|재고|입고|도착|앞당\w*|연기")
_D_ROLE = re.compile(r"기존\s*출하(?:예정)?일|원래\s*출하일|기존|원래|신규\s*출하(?:예정)?일|신규|변경|출하(?:예정)?일|납기|입고|도착|까지")
_UNSUPPORTED = re.compile(
    r"(?:[영일이삼사오육칠팔구십백천만억조]{1,20}|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|"
    r"열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔|몇|수십|수백|수천|반){1,4})(?:여)?\s*(?:개|EA|일)"
    r"(?=$|[\s.,;!?]|가|이|을|를|은|는|만|도|의|간|씩|정도|부터|까지|부족|필요|요청)|"
    r"오늘|내일|모레|익일|당일|금일|명일|작일|내년|올해|하루|이틀|사흘|나흘|"
    r"[월화수목금토일]요일|월말|월초|주말|연말|연초|(?:지난|이번|다음)\s*(?:주|달|월)", re.I)


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def _context(text: str, match: re.Match, tokens: list[re.Match]):
    start, end = 0, len(text)
    for boundary in _BOUNDARY.finditer(text):
        if boundary.end() <= match.start():
            start = boundary.end()
        elif boundary.start() >= match.end():
            end = boundary.start()
            break
    codes = list(CODE.finditer(text, start, match.start()))
    subject = codes[-1].group() if codes else None
    prefix_start = codes[-1].end() if codes else start
    for token in tokens:
        if token.end() <= match.start():
            prefix_start = max(prefix_start, token.end())
        elif token.start() >= match.end():
            end = min(end, token.start())
    following_code = CODE.search(text, match.end(), end)
    if following_code:
        end = following_code.start()
    return subject, text[prefix_start:match.start()], text[match.end():end], text[start:end]


def _role(pattern: re.Pattern, before: str, after: str) -> str:
    preceding = list(pattern.finditer(before))
    if preceding:
        following = pattern.match(re.sub(r'^\s*(?:가|이|을|를)?\s*', '', after))
        if following and _role_kind(preceding[-1].group(), pattern) != _role_kind(following.group(), pattern):
            return ''  # 예: '필요 1040개가 부족'을 필요량 일치로 자동 통과시키지 않는다.
        return preceding[-1].group()
    following = pattern.search(after)
    return following.group() if following else ''


def _role_kind(role: str, pattern: re.Pattern) -> str:
    if pattern is _D_ROLE:
        if role.startswith(('기존', '원래')):
            return 'old_date'
        return 'inbound_date' if role in ('입고', '도착') else 'new_date'
    for names in (('부족', '추가'), ('필요', '소요'), ('확보', '가용'), ('입고', '도착')):
        if role in names:
            return names[0]
    return role


def _inbound(impact: CascadeImpact, subject: str | None, clause: str):
    lines = [line for sf in impact.shortfalls for line in sf.late_arrivals
             if line.material == subject]
    suppliers = [line for line in lines if line.supplier in clause]
    return suppliers or lines


def _quantity_values(impact: CascadeImpact, subject: str | None, role: str,
                     unit: str, clause: str) -> set[int]:
    if unit == '일':
        if role.startswith('앞당') and impact.days_pulled_forward >= 0:
            return {impact.days_pulled_forward}
        if role == '연기' and impact.days_pulled_forward <= 0:
            return {abs(impact.days_pulled_forward)}
        return set()
    if role.startswith('출하') and subject in (None, impact.shipment.product):
        return {impact.shipment.qty}
    if (role == '미완성' or role.startswith('남은')) and subject in (None, impact.shipment.product):
        return {impact.qty_remaining_to_produce}
    sf = next((sf for sf in impact.shortfalls if sf.material == subject), None)
    if sf is None:
        return set()
    if role in ('부족', '추가'):
        return {sf.shortfall}
    if role in ('필요', '소요'):
        return {sf.qty_needed}
    if role in ('확보', '가용'):
        return {sf.qty_available_by_new_date}
    if role in ('입고', '도착'):
        lines = _inbound(impact, subject, clause)
        return {lines[0].qty} if len(lines) == 1 else set()
    return set()


def check_facts(label: str, text: str, impact: CascadeImpact) -> list[Violation]:
    """명확한 불일치=reject(high), 연결/표현이 불명확=review(med).

    허용 날짜: 연도를 포함한 ISO/슬래시/점/한국어 날짜.
    허용 수량: 아라비아 숫자 + 개/EA/일. 그 밖의 숫자는 자동 승인하지 않는다.
    """
    text = unicodedata.normalize('NFKC', text).replace('−', '-')
    spans = [m.span() for m in CODE.finditer(text)]
    dates = list(_DATE.finditer(text))
    quantities = [m for m in _QUANTITY.finditer(text)
                  if not _overlaps(m.span(), spans + [d.span() for d in dates])]
    tokens = sorted(dates + quantities, key=lambda m: m.start())
    violations = []

    def report(category: str, severity: str, detail: str):
        violations.append(Violation(category=category, severity=severity,
                                    detail=f'{label}: {detail}'))

    for match in dates:
        spans.append(match.span())
        subject, before, after, clause = _context(text, match, tokens)
        role = _role(_D_ROLE, before, after)
        if role.startswith(('기존', '원래')):
            expected = {impact.shipment.ship_date}
        elif role in ('입고', '도착'):
            lines = _inbound(impact, subject, clause)
            expected = {lines[0].due_date} if len(lines) == 1 else set()
        elif role:
            expected = {impact.new_ship_date}
        else:
            expected = set()
        try:
            actual = date(*(int(value) for value in match.groups())).isoformat()
        except ValueError:
            report('date_mismatch', 'high', f'유효하지 않은 날짜 {match.group()!r}')
            continue
        if len(expected) != 1:
            report('unverified_numeric_claim', 'med', f'날짜의 대상/항목을 확정할 수 없음: {match.group()!r}')
        elif actual not in expected:
            report('date_mismatch', 'high', f'{role} {actual}, 근거 날짜 {sorted(expected)}')

    for match in quantities:
        spans.append(match.span())
        subject, before, after, clause = _context(text, match, tokens)
        role = _role(_Q_ROLE, before, after)
        expected = _quantity_values(impact, subject, role, match.group(1), clause)
        number = re.sub(r'\s*(?:개|EA|일)$', '', match.group(), flags=re.I)
        # 잘못된 천단위 구분을 조용히 숫자로 바꾸지 않는다.
        if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', number):
            expected = set()
        actual = Decimal(number.replace(',', ''))
        if len(expected) != 1:
            report('unverified_numeric_claim', 'med', f'수량의 대상/항목을 확정할 수 없음: {match.group()!r}')
        elif actual not in expected:
            report('quantity_mismatch', 'high', f'{subject or "출하"} {role} {actual}, 근거 수량 {sorted(expected)}')

    remaining = list(text)
    for start, end in spans:
        remaining[start:end] = ' ' * (end - start)
    if any(char.isnumeric() for char in remaining) or _UNSUPPORTED.search(''.join(remaining)):
        report('unverified_numeric_claim', 'med', '지원하지 않는 숫자·단위 또는 상대 날짜 표현이 있어 확인 필요')
    return violations
