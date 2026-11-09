from typing import TypedDict, Optional
from datetime import datetime


class CaerState(TypedDict, total=False):
    timestamp: str
    grid_snapshot: dict
    macro_snapshot: dict
    polymarket_snapshot: dict
    news_feed: dict
    sentiment_score: float
    contradiction_score: float
    analista_reasoning: dict
    testigo_prediction: Optional[dict]
    case_file: Optional[str]
    tick_count: int


def empty_state() -> CaerState:
    return CaerState(
        timestamp=datetime.utcnow().isoformat(),
        grid_snapshot={},
        macro_snapshot={},
        polymarket_snapshot={},
        news_feed={},
        sentiment_score=0.0,
        contradiction_score=0.0,
        analista_reasoning={},
        testigo_prediction=None,
        case_file=None,
        tick_count=0,
    )
