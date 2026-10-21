"""장소 검색의 핵심 조건과 재시도 횟수를 코드에서 보호합니다."""

import re


class SearchPolicyError(ValueError):
    pass


class MissingRegionError(SearchPolicyError):
    pass


class PlaceSearchPolicy:
    def __init__(self, question, history=None, travel_context=None):
        self.question = question
        user_texts = [m["content"] for m in (history or []) if m.get("role") == "user"] + [question]
        self.user_texts = user_texts
        type_text = question if "여행" in question else " ".join(user_texts)
        self.unrestricted_type = not bool(re.search(r"카페|커피|식당|음식|맛집|숙소|숙박|호텔|관광|명소|박물관|미술관|해산물|바다|해수욕장", type_text))
        self.indoor = False
        for text in user_texts:
            if "실내" in text:
                self.indoor = True
            if "실외" in text or "야외" in text:
                self.indoor = False
        self.allow_other_types = bool(re.search(
            r"(없으면|없다면|없을|없을 때|안\s*나오면|못\s*찾으면).*(다른\s*(종류|유형)|종류.*넓|유형.*넓)", question
        )) and not bool(re.search(r"(넓히지|확장하지|바꾸지)", question))
        self.region = ""
        self.category = None
        self.seen = set()
        self.attempts = 0
        self.context_region = ((travel_context or {}).get("conditions") or {}).get("destination", "")
        match = re.search(r"(?<![가-힣])([가-힣]{2,8})(?:에서|\s*지역)", question)
        self.explicit_region = match.group(1) if match else ""

    def prepare(self, args):
        if self.attempts >= 2:
            raise SearchPolicyError("장소 검색을 두 번 완료했습니다. 추가 검색 전에 조건 변경을 사용자에게 확인해 주세요.")
        query = args.get("query", "")
        category = args.get("category", "")
        if self.unrestricted_type:
            # 사용자가 종류를 지정하지 않았는데 모델이 임의로 좁힌 필터는 적용하지 않습니다.
            category = ""
        region = args.get("region", "")
        if not isinstance(query, str) or not query.strip():
            raise SearchPolicyError("검색할 지역과 장소 종류를 알려 주세요.")
        query = query.strip()
        if not self.region:
            candidate = self.explicit_region or (region.strip() if isinstance(region, str) and region.strip() else (self.context_region or query.split()[0]))
            # 모델이 만든 지역이나 이전 assistant 답변은 사용자 조건의 근거가 아닙니다.
            sources = self.user_texts + [self.context_region]
            generic = {"부모님", "부모님과", "실내", "장소", "여행", "추천", "근처", "우리집", "어디", "전국"}
            if candidate in generic or not any(
                re.search(r"(?<![가-힣])" + re.escape(candidate) + r"(?=$|\s|[,.!?]|요|에서|으로|로|에|의|은|는|을|를|여행|관광|방문|일정)", text)
                for text in sources if isinstance(text, str)
            ):
                raise MissingRegionError("어느 지역의 장소를 찾으시나요?")
            self.region = candidate
            self.category = category
        elif isinstance(region, str) and region.strip() and self.compact(region) != self.compact(self.region):
            raise SearchPolicyError(f"검색 지역을 ‘{self.region}’에서 다른 지역으로 변경해도 될까요?")
        if category != self.category and self.category and not self.allow_other_types:
            raise SearchPolicyError("원래 요청하신 장소 종류의 후보를 찾지 못했습니다. 다른 종류로 넓혀 검색해도 될까요?")
        if self.compact(self.region) not in self.compact(query):
            raise SearchPolicyError(f"‘{self.region}’ 지역을 유지해서는 해당 검색을 진행할 수 없습니다. 지역을 변경해도 될까요?")
        if self.indoor:
            if any(word in query for word in ("야외", "실외", "해수욕장", "해변", "등산", "둘레길")):
                raise SearchPolicyError("실내 조건을 유지한 후보를 찾지 못했습니다. 야외 장소까지 포함해도 될까요?")
            if any(word in query for word in ("박물관", "미술관", "전시관", "과학관", "수족관", "도서관", "영화관")):
                query = " ".join(query.replace("실내", "").split())
            if "실내" not in query and not any(word in query for word in ("박물관", "미술관", "전시관", "과학관", "수족관", "도서관", "영화관", "카페", "식당", "전시")):
                query = self.region + " 실내 " + query.replace(self.region, "").strip()
        signature = (self.compact(query), category)
        if signature in self.seen:
            raise SearchPolicyError("같은 검색 조건은 반복하지 않습니다. 다른 검색어나 장소 종류로 변경해도 될까요?")
        self.seen.add(signature)
        self.attempts += 1
        return {"query": query, "category": category, "region": self.region, "indoor_only": self.indoor}

    @staticmethod
    def compact(value):
        return re.sub(r"\s+", "", value).casefold()
