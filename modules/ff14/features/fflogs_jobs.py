"""FF14-owned job identity and display policy; never rewrite source facts.

Names: official CN event roster and JP/EN Job Guides (links in the module Spec).
The keys are module identities, not FFLogs numeric spec IDs. Only exact known
names/abbreviations resolve; future or unknown source names remain intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

JobLanguage = Literal["zh", "ja", "en"]


@dataclass(frozen=True, slots=True)
class Job:
    key: str
    source_name: str
    zh: str
    ja: str
    abbreviation: str


JOBS = (
    Job("paladin", "Paladin", "骑士", "ナイト", "PLD"),
    Job("warrior", "Warrior", "战士", "戦士", "WAR"),
    Job("dark_knight", "DarkKnight", "暗黑骑士", "暗黒騎士", "DRK"),
    Job("gunbreaker", "Gunbreaker", "绝枪战士", "ガンブレイカー", "GNB"),
    Job("white_mage", "WhiteMage", "白魔法师", "白魔道士", "WHM"),
    Job("scholar", "Scholar", "学者", "学者", "SCH"),
    Job("astrologian", "Astrologian", "占星术士", "占星術師", "AST"),
    Job("sage", "Sage", "贤者", "賢者", "SGE"),
    Job("monk", "Monk", "武僧", "モンク", "MNK"),
    Job("dragoon", "Dragoon", "龙骑士", "竜騎士", "DRG"),
    Job("ninja", "Ninja", "忍者", "忍者", "NIN"),
    Job("samurai", "Samurai", "武士", "侍", "SAM"),
    Job("reaper", "Reaper", "钐镰客", "リーパー", "RPR"),
    Job("viper", "Viper", "蝰蛇剑士", "ヴァイパー", "VPR"),
    Job("bard", "Bard", "吟游诗人", "吟遊詩人", "BRD"),
    Job("machinist", "Machinist", "机工士", "機工士", "MCH"),
    Job("dancer", "Dancer", "舞者", "踊り子", "DNC"),
    Job("black_mage", "BlackMage", "黑魔法师", "黒魔道士", "BLM"),
    Job("summoner", "Summoner", "召唤师", "召喚士", "SMN"),
    Job("red_mage", "RedMage", "赤魔法师", "赤魔道士", "RDM"),
    Job("pictomancer", "Pictomancer", "绘灵法师", "ピクトマンサー", "PCT"),
    Job("blue_mage", "BlueMage", "青魔法师", "青魔道士", "BLU"),
)


def _key(value: str) -> str:
    # FFLogs uses both compact spec names and human-readable spaced names.
    return value.casefold().replace(" ", "").replace("_", "")


_ALIASES = {
    _key(alias): job
    for job in JOBS
    for alias in (job.key, job.source_name, job.abbreviation, job.zh, job.ja)
}


def job_identity(source: str | None) -> Job | None:
    """Resolve a known source identity without fuzzy or translated guessing."""
    return _ALIASES.get(_key(source)) if source is not None else None


def job_display(source: str | None, language: JobLanguage = "zh") -> str:
    """Chinese/Japanese full names; standard English abbreviation."""
    if language not in ("zh", "ja", "en"):
        raise ValueError("unsupported job language")
    if source is None or not source:
        return "—"
    job = job_identity(source)
    if job is None:
        return source
    return {"zh": job.zh, "ja": job.ja, "en": job.abbreviation}[language]


__all__ = ["JOBS", "Job", "JobLanguage", "job_display", "job_identity"]
