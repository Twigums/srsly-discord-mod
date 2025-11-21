from dataclasses import dataclass
from typing import Optional, Dict, Literal, List, Tuple


# init config
@dataclass
class BotConfig:
    srs_app: Optional[object] = None
    token: Optional[str] = None
    prefix: Optional[str] = None
    debug: bool = False

# definition for an interval in config.toml
@dataclass
class Interval:
    value: int
    unit: Literal["hours", "days", "none"]

# srs_app conf
@dataclass
class SrsConfig:
    srs_interval: Dict[int, Interval]
    path_to_srs_db: str
    path_to_full_db: str
    max_reviews_at_once: int = 10
    entries_before_commit: int = 10
    match_score_threshold: int = 85

# colors
@dataclass
class Colors:
    vocab: Tuple[int] = (170, 46, 255) # purple
    kanji: Tuple[int] = (46, 103, 255) # blue
    kana: Tuple[int] = (57, 57, 57) # dark gray
    romaji: Tuple[int] = (228, 228, 228) # light gray

    # list of 5 progressively more greens for stats
    progress: List[Tuple[int]] = None

    def __post_init__(self):
        if self.progress is None:
            self.progress = [(207, 250, 209), (159, 246, 163), (111, 241, 118), (63, 237, 72), (15, 232, 26)]

# card type
class Card:
    review_type: Optional[str] = None
    card_type: Optional[str] = None
    item_id: Optional[int] = None
    readings: Optional[List[str]] = None
    meanings: Optional[List[str]] = None
    kanji: Optional[str] = None
    vocab: Optional[str] = None
