"""M1 data-layer tests — hermetic (mocked HTTP, no token/network needed)."""

from __future__ import annotations

from worldcupagents.dataflows import fifa_rankings
from worldcupagents.dataflows.interface import clear_provider_cache, get_provider
from worldcupagents.dataflows.names import canonical_name, normalize_key
from worldcupagents.dataflows.providers.football_data_org import FootballDataOrgProvider


# --- names + rankings ---

def test_name_aliases():
    assert canonical_name("USA") == "United States"
    assert canonical_name("South Korea") == "Korea Republic"
    assert normalize_key("  Côte   d'Ivoire ") == "cote d ivoire"


def test_rankings_lookup_and_alias():
    assert fifa_rankings.get_rank("Argentina") == 1
    assert fifa_rankings.get_rank("USA") == fifa_rankings.get_rank("United States")
    assert fifa_rankings.get_rank("Narnia") is None


# --- football-data.org provider parsing (mocked HTTP) ---

class FakeHTTP:
    """Returns canned JSON by longest matching URL fragment."""

    def __init__(self, mapping: dict):
        self.mapping = mapping
        self.calls: list[str] = []

    def get_json(self, url, headers=None, ttl=None):
        self.calls.append(url)
        best = None
        for frag, data in self.mapping.items():
            if frag in url and (best is None or len(frag) > len(best[0])):
                best = (frag, data)
        if best is None:
            raise KeyError(url)
        return best[1]


def _provider_with_fixtures() -> FootballDataOrgProvider:
    http = FakeHTTP({
        "competitions/WC/teams": {"teams": [{"id": 1, "name": "Spain"}, {"id": 2, "name": "Germany"}]},
        "teams/1/matches": {"matches": [
            {"homeTeam": {"name": "Spain"}, "awayTeam": {"name": "Germany"},
             "score": {"fullTime": {"home": 2, "away": 1}}, "utcDate": "2026-03-20T19:00:00Z"},
            {"homeTeam": {"name": "France"}, "awayTeam": {"name": "Spain"},
             "score": {"fullTime": {"home": 0, "away": 0}}, "utcDate": "2026-03-23T19:00:00Z"},
        ]},
        "teams/1": {"id": 1, "name": "Spain", "coach": {"name": "L. de la Fuente"},
                    "area": {"name": "Spain"},
                    "squad": [{"name": "Rodri", "position": "Midfield"},
                              {"name": "Lamine Yamal", "position": "Offence"}]},
    })
    return FootballDataOrgProvider(token="x", competition="WC", http=http)


def test_provider_parses_team_profile():
    p = _provider_with_fixtures().get_team_profile("Spain")
    assert p.team == "Spain"
    assert p.fifa_rank == 3                      # from curated table
    assert [pl.name for pl in p.squad] == ["Rodri", "Lamine Yamal"]
    assert "coach: L. de la Fuente" in p.style
    assert len(p.form) == 2
    assert p.form[0].opponent == "Germany" and p.form[0].goals_for == 2


def test_provider_head_to_head_filters_opponent():
    h2h = _provider_with_fixtures().get_head_to_head("Spain", "Germany")
    assert len(h2h) == 1 and h2h[0].opponent == "Germany"


def test_provider_survives_restricted_team_detail():
    """The Wolves bug: /teams/{id} 403s on the free tier, but the competition
    feed carries the squad — the profile must come from there, not go minimal."""
    http = FakeHTTP({
        "competitions/WC/teams": {"teams": [{
            "id": 76, "name": "Wolves",
            "squad": [{"name": "José Sá", "position": "Goalkeeper"},
                      {"name": "Sam Johnstone", "position": "Goalkeeper"}],
        }]},
        # NB: no "teams/76" entry -> any detail/matches lookup raises (= restricted)
    })
    p = FootballDataOrgProvider(token="x", competition="WC", http=http).get_team_profile("Wolves")
    assert [pl.name for pl in p.squad] == ["José Sá", "Sam Johnstone"]   # from the feed
    assert "not_found" not in p.sources[0] and "error" not in p.sources[0]


def test_provider_unknown_team_minimal_profile():
    p = _provider_with_fixtures().get_team_profile("Atlantis")
    assert p.team == "Atlantis"
    assert "not_found" in p.sources[0]


class BoomHTTP:
    def get_json(self, url, headers=None, ttl=None):
        raise RuntimeError("403 Forbidden (bad token)")


def test_provider_degrades_on_http_error():
    """A bad token / network failure must not crash a prediction."""
    prov = FootballDataOrgProvider(token="bad", competition="WC", http=BoomHTTP())
    p = prov.get_team_profile("Spain")
    assert p.team == "Spain"            # canonical name still resolves offline
    assert p.fifa_rank == 3             # rank from curated table
    assert "error" in p.sources[0]
    assert prov.get_recent_results("Spain") == []


# --- registry fallback ---

def test_registry_falls_back_to_placeholder_without_token(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_ORG_TOKEN", raising=False)
    clear_provider_cache()
    cfg = {"data_vendors": {"squads": "football_data_org"}, "cache_dir": ".cache"}
    provider = get_provider(cfg, "squads")
    assert provider.name == "placeholder"   # graceful degradation, no crash
    clear_provider_cache()
