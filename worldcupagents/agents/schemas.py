"""Pydantic schemas — the shared vocabulary of the system.

Prose is still the primary artifact (debate transcripts); structured output is
reserved for the judge's MatchVerdict so probabilities/outcomes are machine-usable
for scoring. Mirrors TradingAgents' "structured output for decision agents only".
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Stage(str, Enum):
    GROUP = "group"
    R32 = "R32"
    R16 = "R16"
    QF = "QF"
    SF = "SF"
    FINAL = "F"


class Outcome(str, Enum):
    HOME_WIN = "HOME_WIN"
    DRAW = "DRAW"
    AWAY_WIN = "AWAY_WIN"


class DecidedBy(str, Enum):
    REGULATION = "regulation"
    EXTRA_TIME = "extra_time"
    PENALTIES = "penalties"


class Player(BaseModel):
    name: str
    position: Optional[str] = None
    club: Optional[str] = None
    status: Literal["fit", "injured", "suspended", "doubt"] = "fit"


class MatchResult(BaseModel):
    opponent: str
    goals_for: int
    goals_against: int
    date: Optional[str] = None
    source: Optional[str] = None  # provenance tag, e.g. "fdcouk:PL:2425"


class Fixture(BaseModel):
    home: str
    away: str
    kickoff: Optional[datetime] = None
    stage: Stage = Stage.GROUP
    venue: Optional[str] = None
    group: Optional[str] = None

    @property
    def knockout(self) -> bool:
        return self.stage != Stage.GROUP


class TeamProfile(BaseModel):
    """The evolving per-nation dossier. Refreshed by scouts; persisted in memory/."""

    team: str
    fifa_rank: Optional[int] = None
    squad: list[Player] = Field(default_factory=list)
    probable_xi: list[str] = Field(default_factory=list)
    formation: Optional[str] = None
    style: str = ""
    form: list[MatchResult] = Field(default_factory=list)
    xg_for: Optional[float] = None
    xg_against: Optional[float] = None
    tournament_pedigree: str = ""
    sources: list[str] = Field(default_factory=list)  # provenance is mandatory
    last_updated: Optional[datetime] = None


class ProbBreakdown(BaseModel):
    """How the final probabilities were formed: the judge's qualitative read and
    the statistical baseline that were blended (weight on the judge)."""

    judge_home: float
    judge_draw: float
    judge_away: float
    base_home: float
    base_draw: float
    base_away: float
    judge_weight: float


class AlternativeOutcome(BaseModel):
    """The 'what if it doesn't go to form' read — the second-most-likely outcome,
    surfaced on every verdict so a favourite call is never the whole story. Honest
    counterweight: favourites lose, draws happen, knockouts go to pens."""

    outcome: Outcome
    probability: float
    scoreline: str
    gap: float                  # primary_prob − alternative_prob (how live it is)
    live: bool                  # a genuinely plausible alternative (not a long shot)
    swing_factors: list[str] = Field(default_factory=list)  # data-backed: HOW it happens
    narrative: str = ""


class MatchVerdict(BaseModel):
    """The judge's structured output."""

    outcome: Outcome
    decided_by: DecidedBy = DecidedBy.REGULATION  # meaningful only for knockouts
    p_home: float
    p_draw: float
    p_away: float
    scoreline: str
    confidence: Literal["low", "medium", "high"] = "medium"
    key_factors: list[str] = Field(default_factory=list)
    x_factors: list[str] = Field(default_factory=list)  # external angles not in debate
    rationale: str = ""
    breakdown: Optional[ProbBreakdown] = None  # judge read vs baseline vs blended
    alternative: Optional[AlternativeOutcome] = None  # the upset/second-most-likely watch
    exp_goals_home: Optional[float] = None  # model's expected goals (λ) — the scoreline is its summary
    exp_goals_away: Optional[float] = None


class JudgeRead(BaseModel):
    """The LLM judge's raw qualitative read, BEFORE ensembling with the baseline.

    Field descriptions double as the model's output instructions (structured output).
    """

    p_home: float = Field(ge=0, le=1, description="Probability HOME team wins")
    p_draw: float = Field(ge=0, le=1, description="Probability of a draw (≈0 for knockouts)")
    p_away: float = Field(ge=0, le=1, description="Probability AWAY team wins")
    scoreline: str = Field(description="Most likely scoreline, e.g. '2-1'")
    confidence: Literal["low", "medium", "high"] = "medium"
    key_factors: list[str] = Field(default_factory=list, description="Decisive factors from the debate")
    x_factors: list[str] = Field(default_factory=list, description="External angles the advocates under-weighted")
    rationale: str = Field(default="", description="2-4 sentence justification of the verdict")


# ── Commentary → tactical-insight pipeline (COMMENTARY_PLAN.md, slice A→B→C) ──

# The five logical phases a match timeline is grouped into. Order matters:
# downstream code iterates PHASE_LABELS to produce a chunk per phase.
PHASE_INITIAL = "0-15 Initial Setup"
PHASE_FIRST_HALF = "15-45 First-Half Shift"
PHASE_HALF_TIME = "Half-Time Brief"
PHASE_ADJUSTMENTS = "45-75 Tactical Adjustments"
PHASE_CRUNCH = "75-90+ Crunch Time"
PHASE_LABELS = [
    PHASE_INITIAL,
    PHASE_FIRST_HALF,
    PHASE_HALF_TIME,
    PHASE_ADJUSTMENTS,
    PHASE_CRUNCH,
]


class MatchEvent(BaseModel):
    """A typed timeline event (from the stats API, not parsed from prose)."""

    minute: int
    type: Literal["goal", "card", "sub", "var", "other"] = "other"
    detail: str = ""  # e.g. "Messi (pen)", "Yellow — Otamendi"


class CommentaryEntry(BaseModel):
    """One beat of qualitative text commentary, optionally minute-stamped."""

    minute: Optional[int] = None  # base minute (added time folded into the phase)
    text: str


class PhaseChunk(BaseModel):
    """All commentary + events belonging to one of the five match phases."""

    phase: str
    entries: list[CommentaryEntry] = Field(default_factory=list)
    events: list[MatchEvent] = Field(default_factory=list)


class PhaseTacticalInsight(BaseModel):
    """The tactical analyst's structured read of a single phase.

    Field descriptions double as structured-output instructions for the LLM.
    """

    phase: str
    formations_blocks: list[str] = Field(
        default_factory=list,
        description="Formations and defensive/pressing blocks, e.g. 'low-block', '4-3-3 high press'",
    )
    adjustments: list[str] = Field(
        default_factory=list,
        description="Notable tactical adjustments, e.g. 'winger shifting inside', 'dropped to a back five'",
    )
    key_matchups: list[str] = Field(
        default_factory=list,
        description="Individual player matchups the commentary highlights",
    )
    summary: str = Field(default="", description="2-3 sentence synopsis of this phase")


class MatchTacticalReport(BaseModel):
    """The full per-match tactical report, persisted to memory/matches/."""

    match_id: str
    home: str
    away: str
    date: Optional[str] = None
    phases: list[PhaseTacticalInsight] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # provenance is mandatory


class PlayerStat(BaseModel):
    """Per-player metrics (from the scorers feed). Goal-contribution today;
    a richer source (passing accuracy, xG) can extend this later."""

    player: str
    team: str = ""
    comp: str = ""
    goals: int = 0
    assists: int = 0
    penalties: int = 0
    matches: int = 0
    # Richer metrics (populated by API-Football / Understat; None from the basic feed)
    pass_accuracy: Optional[float] = None  # %
    key_passes: Optional[int] = None
    minutes: Optional[int] = None
    rating: Optional[float] = None
    shots: Optional[int] = None
    xg: Optional[float] = None
    xa: Optional[float] = None
    xg_buildup: Optional[float] = None  # possession-chain involvement (Understat)
    source: str = ""

    @property
    def goal_contributions(self) -> int:
        return (self.goals or 0) + (self.assists or 0)


class CriticFinding(BaseModel):
    """One cross-examination: a hard metric explained by qualitative evidence."""

    metric: str = Field(description="The quantitative observation, e.g. 'conceded 1.4 xG/game'")
    commentary: str = Field(description="The tactical evidence that explains it, from the analysed matches")
    insight: str = Field(description="The synthesised deep context — WHY the number looks this way")


class CriticReport(BaseModel):
    """Critic Loop output — quantitative metrics cross-examined against qualitative
    tactical commentary to surface deep context."""

    team: str
    summary: str = Field(default="", description="2-4 sentence synthesis")
    findings: list[CriticFinding] = Field(default_factory=list)
    tensions: list[str] = Field(
        default_factory=list, description="Where the numbers and the commentary disagree or can't be reconciled"
    )
    sources: list[str] = Field(default_factory=list)


class ScoutReport(BaseModel):
    """Senior-Scout 'Contextual Performance Report' — blends squad/stats with the
    tactical tendencies mined from analysed matches. Field descriptions double as
    structured-output instructions for the LLM."""

    team: str
    summary: str = Field(default="", description="2-4 sentence scouting overview")
    strengths: list[str] = Field(default_factory=list, description="What this team does well")
    weaknesses: list[str] = Field(default_factory=list, description="Exploitable vulnerabilities")
    tactical_tendencies: list[str] = Field(
        default_factory=list, description="Recurring shapes/blocks/adjustments seen across analysed matches"
    )
    key_players: list[str] = Field(default_factory=list, description="Players who decide matches for this team")
    sources: list[str] = Field(default_factory=list)
