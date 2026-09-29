"""Deterministic candidate facts. Source chunks retain full table context."""

import re
from decimal import Decimal

from app.extraction.grounding import evidence_segments
from app.extraction.schemas import Claim, ClaimExtractionResponse

DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b", re.I)
NUMBER = re.compile(r"(?<![\w.])(?P<currency>[$€£₹]|USD\s*|EUR\s*|GBP\s*|INR\s*)?(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s*(?P<scale>thousand|million|billion|crore|lakh))?(?:\s*(?P<unit>%|percent\b|employees\b|people\b|units\b|tonnes\b|kg\b|km\b|MW\b))?", re.I)
METRICS = re.compile(r"\b(revenue|sales|net profit|profit|operating margin|margin|employee count|employees|headcount|capacity|production|assets|liabilities|cost|expenses|customers|deadline|launch date)\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class LocalFactExtractor:
    def extract(self, chunk):
        claims = []
        for segment in evidence_segments(chunk):
            if segment["page"] is None:
                continue
            headers = []
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", segment["text"]):
                sentence = sentence.strip()
                if not sentence:
                    continue
                cells = [cell.strip() for cell in sentence.strip("|").split("|")]
                table = "|" in sentence
                if table and all(re.fullmatch(r"[:\- ]*", cell) for cell in cells):
                    continue
                if table and not headers:
                    headers = cells
                    continue
                dates = list(DATE.finditer(sentence))
                years = YEAR.findall(sentence)
                metrics = list(METRICS.finditer(sentence))
                entity = re.split(r"\b(?:has|had|reported|reports|recorded|revenue|sales|net profit|profit|employees|headcount)\b", sentence, maxsplit=1, flags=re.I)[0].strip(" |:,-")
                # Unknown entities must not become invented company names.
                entity = entity if entity and len(entity.split()) <= 6 and not table else "document subject"
                scope = re.search(r"\b(global(?:ly)?|domestic|international|consolidated|standalone)\b", sentence, re.I)
                for match in [*dates, *NUMBER.finditer(sentence)]:
                    is_date = match.re is DATE
                    if not is_date and any(d.start() <= match.start() < d.end() for d in dates):
                        continue
                    if not is_date and YEAR.fullmatch(match.group().strip()) and (metrics or table):
                        continue
                    metric = min(metrics, key=lambda m: abs(m.start() - match.start())).group().lower() if metrics else ""
                    column = sentence[:match.start()].count("|") - (1 if sentence.startswith("|") else 0)
                    header = headers[column] if table and 0 <= column < len(headers) else ""
                    if table:
                        metric = cells[0] or metric or "table value"
                    if not metric:
                        metric = NUMBER.sub(" ", sentence[:match.start()]).strip(" :|,.-")[-100:] or ("date" if is_date else "quantity")
                    period_years = YEAR.findall(header) if table else years
                    period = period_years[0] if len(set(period_years)) == 1 else None
                    if is_date:
                        period = None  # A deadline is the value, not its reporting period.
                    unit = None
                    if not is_date:
                        currency = match.group("currency")
                        currency = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}.get(currency, currency)
                        unit = " ".join(part.strip() for part in (currency, match.group("scale"), match.group("unit")) if part) or None
                    claims.append(Claim(entity_raw=entity, entity_normalized=None,
                        metric_raw=metric, metric_normalized=None, value_text=match.group().strip(),
                        value_numeric=None if is_date else Decimal(match.group("number").replace(",", "")),
                        unit=unit, period=period, scope=scope.group().lower().replace("globally", "global") if scope else None,
                        qualifier=(f"Table column: {header}" if header else None),
                        claim_text=sentence, source_page=segment["page"], confidence=Decimal("0.6")))
        return ClaimExtractionResponse(claims=claims)
