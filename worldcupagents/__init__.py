"""WorldCupAgents — multi-agent FIFA 2026 match predictor (inspired by TradingAgents)."""

__version__ = "0.1.0"

from worldcupagents.agents.schemas import Fixture, Stage, MatchVerdict, Outcome  # noqa: E402
from worldcupagents.graph.predict import Predictor  # noqa: E402

__all__ = ["Fixture", "Stage", "MatchVerdict", "Outcome", "Predictor", "__version__"]
