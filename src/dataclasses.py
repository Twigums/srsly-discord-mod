from dataclasses import dataclass
from typing import Optional, Dict, Literal, List


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
    vocab: str = "#aa2eff"
    kanji: str = "#2e67ff"
    kana: str = "#393939"
    romaji: str = "#e4e4e4"

    # list of 5 progressively more greens for stats
    progress: List[str] = None

    def __post_init__(self):
        if self.progress is None:
            self.progress = ["#cffad1", "#9ff6a3", "#6ff176", "#3fed48", "#0fe81a"]

# card type
class Card:
    review_type: Optional[str] = None
    card_type: Optional[str] = None
    item_id: Optional[int] = None
    readings: Optional[List[str]] = None
    meanings: Optional[List[str]] = None
    kanji: Optional[str] = None
    vocab: Optional[str] = None
